"""Utility functions for Atlas infrastructure."""
import asyncio
import functools
import logging
from typing import Any, Callable, Optional, TypeVar, cast

logger = logging.getLogger("atlas.utils")

T = TypeVar("T")


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """
    Retry decorator for async functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry
    """
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


