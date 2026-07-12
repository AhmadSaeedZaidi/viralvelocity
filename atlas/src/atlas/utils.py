"""Utility functions for Atlas infrastructure."""

import asyncio
import functools
import itertools
import logging
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger("atlas.utils")

T = TypeVar("T")


class QuotaExhaustedError(Exception):
    """Raised when all API keys in a KeyRing are exhausted for a request.

    A normal ``Exception`` (not ``SystemExit``) so callers decide the resiliency
    action and it never tears down an unrelated host process importing Atlas.
    """


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    max_delay: float = 60.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Async retry decorator with exponential backoff and capped delay."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            last_exception: BaseException | None = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_attempts}): {e}"
                        )
                        sleep_time = min(current_delay, max_delay)
                        await asyncio.sleep(sleep_time)
                        current_delay *= backoff
                    else:
                        logger.exception(f"{func.__name__} failed after {max_attempts} attempts")

            if last_exception:
                raise last_exception
            return None

        return wrapper

    return decorator


async def health_check_all() -> dict[str, bool]:
    """Health-check all Atlas components; returns a mapping of name to status."""
    from atlas import db

    results = {}

    try:
        results["database"] = await db.health_check()
    except Exception as e:
        logger.exception(f"Database health check failed: {e}")
        results["database"] = False

    return results


def validate_youtube_id(video_id: str) -> bool:
    """Validate an 11-char base64url YouTube video ID."""
    if not video_id or not isinstance(video_id, str):
        return False

    if len(video_id) != 11:
        return False

    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    return all(c in allowed_chars for c in video_id)


def validate_channel_id(channel_id: str) -> bool:
    """Validate a 24-char UC-prefixed YouTube channel ID."""
    if not channel_id or not isinstance(channel_id, str):
        return False

    if not channel_id.startswith("UC"):
        return False

    if len(channel_id) != 24:
        return False

    allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    return all(c in allowed_chars for c in channel_id)


class KeyRing:
    """Manages API key rotation with exhaustible session tracking."""

    def __init__(self, pool_name: str):
        from atlas.config import settings

        self.pool_name = pool_name.lower()
        self.keys: list[str] = settings.key_rings.get(self.pool_name, [])

        if not self.keys:
            logger.error(f"KeyRing: No keys initialized for pool '{pool_name}'!")
            raise ValueError(f"Empty KeyRing for {pool_name}")

        self._iterator: itertools.cycle[str] = itertools.cycle(self.keys)

        # A monotonic counter guarantees unique session ids so concurrent
        # calls never clobber each other's attempt counts.
        self._session_counter = itertools.count()
        self._current_session_attempts: dict[int, int] = {}

        logger.info(f"KeyRing: Initialized '{pool_name}' with {len(self.keys)} keys.")

    def next_key(self) -> str:
        """Get the next key from the infinite cycle."""
        return next(self._iterator)

    def start_session(self, session_id: int | None = None) -> int:
        """Start a new exhaustible rotation session; allocates a unique id if
        none given. Returns the session id."""

        if session_id is None:
            session_id = next(self._session_counter)
        self._current_session_attempts[session_id] = 0
        return session_id

    def get_session_key(self, session_id: int) -> str:
        """Return the next key for this session (round-robin through the pool)."""
        attempt = self._current_session_attempts.get(session_id, 0)
        key_index = attempt % len(self.keys)
        return self.keys[key_index]

    def attempt_rotation(self, session_id: int) -> bool:
        """Rotate to the next key; return True if more keys remain, False if exhausted."""
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
                f"KeyRing '{self.pool_name}': All {len(self.keys)} keys exhausted"
                f" for session {session_id}"
            )

        return has_more

    def end_session(self, session_id: int) -> None:
        """Clean up session tracking."""
        self._current_session_attempts.pop(session_id, None)

    @property
    def size(self) -> int:
        return len(self.keys)


class ResiliencyExecutor:
    """Execute API requests with key rotation and resiliency termination.

    On quota exhaustion raises :class:`QuotaExhaustedError` so the caller (Maia
    agent layer) can act (typically restarting the container for IP rotation);
    a catchable exception, not ``sys.exit``, so it never kills an unrelated
    host process importing Atlas.
    """

    def __init__(self, key_ring: KeyRing, agent_name: str = "unknown"):
        self.key_ring = key_ring
        self.agent_name = agent_name
        self.logger = logging.getLogger(f"atlas.resiliency.{agent_name}")

    async def execute_async(
        self,
        request_func: Callable[[str], Any],
        error_classifier: Callable[[Exception], tuple[bool, bool]] | None = None,
    ) -> Any | None:
        """Execute an API request with key rotation; raises
        QuotaExhaustedError when all keys are exhausted."""
        session_id = self.key_ring.start_session()

        try:
            while True:
                key = self.key_ring.get_session_key(session_id)

                try:
                    result = await request_func(key)
                    self.logger.debug(f"Request succeeded with key {key[-6:]}")
                    return result

                except Exception as e:
                    if error_classifier:
                        is_quota_error, is_retryable = error_classifier(e)
                    else:
                        is_quota_error = self._is_quota_error(e)
                        is_retryable = is_quota_error

                    if is_quota_error:
                        self.logger.warning(f"Quota error with key {key[-6:]}: {e}")

                        if self.key_ring.attempt_rotation(session_id):
                            continue  # Retry with next key
                        # All keys exhausted — raise a catchable
                        # QuotaExhaustedError so the caller decides the resiliency action.
                        self.logger.critical(
                            f"RESILIENCY: All keys exhausted for {self.agent_name}. "
                            f"Signalling quota exhaustion to caller."
                        )
                        raise QuotaExhaustedError(
                            f"All API keys exhausted for {self.agent_name}"
                        ) from e

                    if is_retryable:
                        self.logger.warning(f"Retryable error: {e}")
                        if self.key_ring.attempt_rotation(session_id):
                            continue
                        self.logger.exception("All retry attempts exhausted")
                        return None
                    self.logger.exception(f"Non-retryable error: {e}")
                    raise

        finally:
            self.key_ring.end_session(session_id)

    def _is_quota_error(self, exception: Exception) -> bool:
        """Detect quota errors, deliberately excluding bare 403 (private/region-blocked videos)."""
        error_str = str(exception).lower()

        # Exclude bare 403 (often a private/region-blocked video, and YouTube may
        # embed "quotaExceeded" in its body); the word-boundary check avoids matching "34038".
        if "http 403" in error_str:
            return False

        # Common quota error indicators
        quota_indicators = [
            "quota",
            "rate limit",
            "429",
            "quotaexceeded",
            "usagelimit",
        ]

        return any(indicator in error_str for indicator in quota_indicators)


async def execute_youtube_request_async(
    key_ring: KeyRing,
    request_func: Callable[[str], Any],
    agent_name: str = "youtube_api",
) -> Any | None:
    """Execute a YouTube API request with key rotation and resiliency termination."""
    executor = ResiliencyExecutor(key_ring, agent_name)
    return await executor.execute_async(request_func)
