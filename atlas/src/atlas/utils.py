"""Utility functions for Atlas infrastructure."""
import asyncio
import functools
import itertools
import logging
import sys
from typing import Any, Callable, Optional, TypeVar, cast, Dict

logger = logging.getLogger("atlas.utils")

T = TypeVar("T")


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Async retry decorator with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_attempts}): {e}"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts"
                        )
            
            if last_exception:
                raise last_exception
            
        return wrapper
    return decorator


async def health_check_all() -> dict[str, bool]:
    """
    Perform health checks on all Atlas components.
    
    Returns:
        Dictionary mapping component names to health status
    """
    from atlas import db
    
    results = {}
    
    try:
        results["database"] = await db.health_check()
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        results["database"] = False
    
    return results


def validate_youtube_id(video_id: str) -> bool:
    """
    Validate YouTube video ID format.
    
    Args:
        video_id: YouTube video ID to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not video_id or not isinstance(video_id, str):
        return False
    
    if len(video_id) != 11:
        return False
    
    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    return all(c in allowed_chars for c in video_id)


def validate_channel_id(channel_id: str) -> bool:
    """
    Validate YouTube channel ID format.
    
    Args:
        channel_id: YouTube channel ID to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not channel_id or not isinstance(channel_id, str):
        return False
    
    if not channel_id.startswith("UC"):
        return False
    
    if len(channel_id) != 24:
        return False
    
    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    return all(c in allowed_chars for c in channel_id)


class KeyRing:
    """
    Manages API key rotation with exhaustible session tracking.
    
    Supports both infinite cycling (for multi-request operations) and 
    session-based exhaustion tracking (for single-request retry logic).
    """
    
    def __init__(self, pool_name: str):
        from atlas.config import settings
        
        self.pool_name = pool_name.lower()
        self.keys = settings.key_rings.get(self.pool_name, [])
        
        if not self.keys:
            logger.error(f"KeyRing: No keys initialized for pool '{pool_name}'!")
            raise ValueError(f"Empty KeyRing for {pool_name}")
            
        self._iterator = itertools.cycle(self.keys)
        
        # Session tracking for exhaustible rotation
        self._current_session_attempts: Dict[int, int] = {}
        
        logger.info(f"KeyRing: Initialized '{pool_name}' with {len(self.keys)} keys.")
    
    def next_key(self) -> str:
        """Get next key from the infinite cycle (original behavior)."""
        return next(self._iterator)
    
    def start_session(self, session_id: Optional[int] = None) -> int:
        """
        Start a new exhaustible rotation session.
        
        Args:
            session_id: Optional custom session ID (uses id() if not provided)
            
        Returns:
            Session ID to use for tracking
        """
        if session_id is None:
            session_id = id(self)
        
        self._current_session_attempts[session_id] = 0
        return session_id
    
    def get_session_key(self, session_id: int) -> str:
        """
        Get the next key for this session (round-robin through pool).
        
        Args:
            session_id: The session ID from start_session()
            
        Returns:
            API key for this attempt
        """
        attempt = self._current_session_attempts.get(session_id, 0)
        key_index = attempt % len(self.keys)
        return self.keys[key_index]
    
    def attempt_rotation(self, session_id: int) -> bool:
        """
        Attempt to rotate to the next key in this session.
        
        Args:
            session_id: The session ID from start_session()
            
        Returns:
            True if there are more keys to try, False if pool is exhausted
        """
        if session_id not in self._current_session_attempts:
            logger.warning(f"Session {session_id} not found, initializing")
            self._current_session_attempts[session_id] = 0
        
        self._current_session_attempts[session_id] += 1
        attempts = self._current_session_attempts[session_id]
        
        has_more = attempts < len(self.keys)
        
        if has_more:
            logger.info(
                f"KeyRing '{self.pool_name}': Rotating to key {attempts + 1}/{len(self.keys)}"
            )
        else:
            logger.critical(
                f"KeyRing '{self.pool_name}': All {len(self.keys)} keys exhausted for session {session_id}"
            )
        
        return has_more
    
    def end_session(self, session_id: int) -> None:
        """Clean up session tracking."""
        self._current_session_attempts.pop(session_id, None)

    @property
    def size(self) -> int:
        return len(self.keys)


class HydraExecutor:
    """
    Unified executor for Google API requests with Hydra Protocol termination.
    
    The Hydra Protocol:
    - Exit 0 (Clean Death): All keys exhausted for a request → Container restarts cleanly
    - Exit 1 (Dirty Death): Unexpected error → Container restarts with error signal
    
    This prevents infinite retry loops and enforces clean termination on quota exhaustion.
    """
    
    def __init__(self, key_ring: KeyRing, agent_name: str = "unknown"):
        self.key_ring = key_ring
        self.agent_name = agent_name
        self.logger = logging.getLogger(f"atlas.hydra.{agent_name}")
    
    async def execute_async(
        self,
        request_func: Callable[[str], Any],
        error_classifier: Optional[Callable[[Exception], tuple[bool, bool]]] = None
    ) -> Optional[Any]:
        """
        Execute an async API request with key rotation and Hydra Protocol termination.
        
        Args:
            request_func: Async function that takes an API key and returns result
            error_classifier: Optional function that takes an exception and returns
                            (is_quota_error, is_retryable) tuple
        
        Returns:
            API response or None on non-quota errors
            
        Raises:
            SystemExit: On key exhaustion (exit code 0) or critical errors
        """
        session_id = self.key_ring.start_session()
        
        try:
            while True:
                key = self.key_ring.get_session_key(session_id)
                
                try:
                    # Execute the request
                    result = await request_func(key)
                    self.logger.debug(f"Request succeeded with key {key[-6:]}")
                    return result
                    
                except Exception as e:
                    # Classify the error
                    if error_classifier:
                        is_quota_error, is_retryable = error_classifier(e)
                    else:
                        # Default: treat 403/429 as quota errors
                        is_quota_error = self._is_quota_error(e)
                        is_retryable = is_quota_error
                    
                    if is_quota_error:
                        self.logger.warning(
                            f"Quota error with key {key[-6:]}: {e}"
                        )
                        
                        # Try rotating to next key
                        if self.key_ring.attempt_rotation(session_id):
                            continue  # Retry with next key
                        else:
                            # All keys exhausted - Hydra Protocol: Clean Death
                            self.logger.critical(
                                f"🔥 HYDRA PROTOCOL: All keys exhausted for {self.agent_name}. "
                                f"Initiating clean container termination (exit 0)."
                            )
                            sys.exit(0)  # Clean death - container will restart
                    
                    elif is_retryable:
                        # Retryable non-quota error (network issues, etc.)
                        self.logger.warning(f"Retryable error: {e}")
                        if self.key_ring.attempt_rotation(session_id):
                            continue
                        else:
                            self.logger.error("All retry attempts exhausted")
                            return None
                    else:
                        # Non-retryable error - propagate
                        self.logger.error(f"Non-retryable error: {e}")
                        raise
        
        finally:
            self.key_ring.end_session(session_id)
    
    def _is_quota_error(self, exception: Exception) -> bool:
        """Default quota error detection."""
        error_str = str(exception).lower()
        
        # Common quota error indicators
        quota_indicators = [
            "quota",
            "rate limit",
            "429",
            "403",
            "quotaexceeded",
            "usagelimit",
        ]
        
        return any(indicator in error_str for indicator in quota_indicators)


async def execute_youtube_request_async(
    key_ring: KeyRing,
    request_func: Callable[[str], Any],
    agent_name: str = "youtube_api"
) -> Optional[Any]:
    """
    Convenience function for executing YouTube API requests with Hydra Protocol.
    
    Args:
        key_ring: KeyRing instance for key rotation
        request_func: Async function that takes an API key and returns result
        agent_name: Name of the calling agent (for logging)
    
    Returns:
        API response or None
        
    Raises:
        SystemExit: On key exhaustion (Hydra Protocol)
    """
    executor = HydraExecutor(key_ring, agent_name)
    return await executor.execute_async(request_func)
