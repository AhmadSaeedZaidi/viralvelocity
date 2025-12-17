import time
from functools import wraps
from typing import Any, Dict, Tuple

# Simple in-memory storage
_memory_cache: Dict[str, Tuple[Any, float]] = {}


def time_based_cache(seconds: int = 60):
    """
    A lightweight decorator to cache function results in memory.
    Useful for things like 'get_model_status' or frequent tag lookups.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create a unique key based on function name and arguments
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            current_time = time.time()

            # Check if key exists and is still valid
            if key in _memory_cache:
                result, timestamp = _memory_cache[key]
                if current_time - timestamp < seconds:
                    return result

            # Compute and store
            result = func(*args, **kwargs)
            _memory_cache[key] = (result, current_time)
            return result

        return wrapper

    return decorator


def clear_cache():
    """Clears the internal memory cache."""
    _memory_cache.clear()
