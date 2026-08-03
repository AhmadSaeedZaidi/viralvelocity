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

        # Keys observed to fail with an unrecoverable auth/quota error (e.g. a
        # revoked API key returning 403). Blacklisted keys are never handed out
        # again within this process so a few dead keys can't wedge the whole
        # ring. The ring recovers automatically as soon as at least one live key
        # remains; if *every* key is blacklisted the ring is genuinely exhausted.
        self._dead_keys: set[str] = set()
        self._live_keys: list[str] = list(self.keys)

        # A monotonic counter guarantees unique session ids so concurrent
        # calls never clobber each other's attempt counts.
        self._session_counter = itertools.count()
        self._current_session_attempts: dict[int, int] = {}

        logger.info(f"KeyRing: Initialized '{pool_name}' with {len(self.keys)} keys.")

    def next_key(self) -> str:
        """Get the next key from the infinite cycle."""
        return next(self._iterator)

    def mark_key_dead(self, key: str) -> None:
        """Permanently exclude ``key`` from this ring for the process lifetime.

        Called when a key fails with an unrecoverable auth/quota error so the
        ring keeps working with its remaining live keys instead of cycling back
        onto the same dead key.
        """
        if key in self._dead_keys:
            return
        self._dead_keys.add(key)
        self._live_keys = [k for k in self.keys if k not in self._dead_keys]
        logger.warning(
            f"KeyRing '{self.pool_name}': blacklisted dead key {key[-6:]} "
            f"({len(self._live_keys)}/{len(self.keys)} still live)"
        )

    @property
    def live_size(self) -> int:
        """Number of keys still considered usable."""
        return len(self._live_keys) or len(self.keys)

    def start_session(self, session_id: int | None = None) -> int:
        """Start a new exhaustible rotation session; allocates a unique id if
        none given. Returns the session id."""

        if session_id is None:
            session_id = next(self._session_counter)
        self._current_session_attempts[session_id] = 0
        return session_id

    def get_session_key(self, session_id: int) -> str:
        """Return the next key for this session, skipping blacklisted keys."""
        attempt = self._current_session_attempts.get(session_id, 0)
        pool = self._live_keys if self._live_keys else self.keys
        key_index = attempt % len(pool)
        return pool[key_index]

    def attempt_rotation(self, session_id: int) -> bool:
        """Rotate to the next key; return True if more live keys remain, False if exhausted."""
        if session_id not in self._current_session_attempts:
            logger.warning(f"Session {session_id} not found, initializing")
            self._current_session_attempts[session_id] = 0

        self._current_session_attempts[session_id] += 1
        attempts = self._current_session_attempts[session_id]

        has_more = attempts < self.live_size

        if has_more:
            logger.info(
                f"KeyRing '{self.pool_name}': Rotating to key {attempts + 1}/{self.live_size}"
            )
        else:
            logger.critical(
                f"KeyRing '{self.pool_name}': All {self.live_size} live keys exhausted"
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
                        # A 403 (revoked/invalid key) is permanently dead for
                        # this process; blacklist it so the ring keeps working
                        # with its remaining live keys. A 429 is a transient
                        # rate-limit and must NOT be blacklisted — it recovers
                        # after the quota window resets, and blacklisting it
                        # would eventually exhaust the whole fleet.
                        if "http 403" in str(e).lower() or "http 401" in str(e).lower():
                            self.key_ring.mark_key_dead(key)

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
        """Detect quota/key-exhaustion errors that warrant rotating to the next key.

        ``403`` from the YouTube Data API almost always means the API key is
        revoked or invalid (a *dead* key) — the ring is used for keyed
        search/list calls, so a 403 must trigger rotation to a live key rather
        than being raised as non-retryable. ``429`` is a *transient* rate-limit
        and also rotates, but the caller must NOT permanently blacklist a 429'd
        key (it recovers after the quota window resets).
        """
        error_str = str(exception).lower()
        # HTTP status codes (with or without the "http" prefix) signal key
        # problems: 401/403 = revoked/dead key, 429 = transient rate-limit.
        # Both must rotate to the next key.
        if any(f"{code}" in error_str for code in ("http 401", "http 403", "http 429", "401", "403", "429")):
            return True
        quota_indicators = [
            "quota",
            "rate limit",
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
