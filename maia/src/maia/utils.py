"""Shared utilities for Maia agents.

Centralises helpers that were previously duplicated across agent modules
(retry wrappers, executor helpers, common exceptions).
"""

import asyncio
import functools
import logging
import re
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# ── Rate-limit detection patterns ────────────────────────────────────────────

YT_RATE_LIMIT_PATTERNS = [
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"sign in to confirm", re.IGNORECASE),
    re.compile(r"this content isn.t available.*try again later", re.IGNORECASE),
    re.compile(r"unable to extract", re.IGNORECASE),
    re.compile(r"http error 403", re.IGNORECASE),
]


def looks_like_rate_limit(stderr: str) -> bool:
    """Heuristic check — does *stderr* contain known YouTube rate-limit signals?"""
    return any(p.search(stderr) for p in YT_RATE_LIMIT_PATTERNS)


# ── Adaptive concurrency controller ──────────────────────────────────────────


async def _notify_rate_limit_downgrade(old_conc: int, new_conc: int, rate: float) -> None:
    """Fire-and-forget Discord alert when AdaptiveConcurrency downgrades."""
    try:
        from atlas.notifications import AlertChannel, AlertLevel, notifier

        await notifier.send(
            title="⚠ Concurrency Downgraded (Rate-Limit Pressure)",
            description=(
                f"AdaptiveConcurrency detected {rate * 100:.0f}% failure rate "
                f"and lowered concurrency from **{old_conc}** → **{new_conc}**.\n\n"
                "The scraper is self-throttling to avoid being blocked. "
                "If this persists, consider reducing batch sizes or "
                "adding a longer cooldown between cycles."
            ),
            channel=AlertChannel.ALERTS,
            level=AlertLevel.WARNING,
            fields={
                "Previous Concurrency": str(old_conc),
                "New Concurrency": str(new_conc),
                "Failure Rate": f"{rate * 100:.0f}%",
            },
        )
    except Exception:
        logger.exception("Failed to send rate-limit downgrade notification")


class AdaptiveConcurrency:
    """Sliding-window rate-limit monitor that dynamically lowers concurrency.

    Tracks 429-like failures in a rolling time window.  If the failure rate
    exceeds *max_failure_rate* the concurrency is halved (min 1).  After a
    quiet period the concurrency is slowly restored.

    Usage::

        acc = AdaptiveConcurrency(max_concurrent=5, window_secs=300)
        async with acc.sem:
            ok = await do_work(...)
        acc.record(ok)
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        window_secs: int = 300,
        max_failure_rate: float = 0.05,
        recovery_step: int = 1,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._window_secs = window_secs
        self._max_failure_rate = max_failure_rate
        self._recovery_step = recovery_step
        self._concurrency = max_concurrent

        # Rolling window: list of (timestamp, was_failure)
        self._window: list[tuple[float, bool]] = []
        self._last_downgrade = 0.0

    @property
    def sem(self) -> asyncio.Semaphore:
        return self._sem

    def record(self, ok: bool) -> None:
        now = time.monotonic()
        self._window.append((now, not ok))
        # Purge entries older than the window
        cutoff = now - self._window_secs
        self._window = [(ts, f) for ts, f in self._window if ts > cutoff]

        # Compute failure rate
        if len(self._window) < 10:
            return
        failures = sum(1 for _, f in self._window if f)
        rate = failures / len(self._window)

        if rate > self._max_failure_rate and self._concurrency > 1:
            new_conc = max(1, self._concurrency // 2)
            # Send Discord alert on first downgrade (cooldown 10 min)
            if now - self._last_downgrade > 600:
                asyncio.ensure_future(
                    _notify_rate_limit_downgrade(self._concurrency, new_conc, rate)
                )
            logger.warning(
                "AdaptiveConcurrency: failure rate %.1f%% (>%.0f%%) "
                "→ lowering concurrency %d→%d",
                rate * 100,
                self._max_failure_rate * 100,
                self._concurrency,
                new_conc,
            )
            self._concurrency = new_conc
            self._sem = asyncio.Semaphore(self._concurrency)
            self._last_downgrade = now
        elif rate < self._max_failure_rate / 2 and self._concurrency < self._max_concurrent:
            # Slowly recover
            new_conc = min(self._concurrency + self._recovery_step, self._max_concurrent)
            self._concurrency = new_conc
            self._sem = asyncio.Semaphore(self._concurrency)


async def notify_quota_exhausted(agent_name: str) -> None:
    """Send a Discord alert that all API keys are exhausted for *agent_name*.

    The alert is **rate-limited**: it fires at most once per cooldown window
    (``atlas.state.QUOTA_ALERT_COOLDOWN_S``) per agent, so the resiliency
    restart/retry loop does not spam Discord on every attempt. The exhausted
    state is also recorded so the heartbeat can show a "rate limited" status.

    On a VPS with no container rotation this is the only signal — the
    caller should back off for a period rather than retrying immediately.
    """
    from atlas.state import (
        mark_quota_exhausted,
        record_quota_alert,
        should_send_quota_alert,
    )

    mark_quota_exhausted(agent_name)
    if not should_send_quota_alert(agent_name):
        logger.info(
            f"Quota-exhausted alert for {agent_name} suppressed (within cooldown)."
        )
        return

    try:
        from atlas.notifications import AlertChannel, AlertLevel, notifier

        await notifier.send(
            title=f"⚠ Quota Exhausted: {agent_name}",
            description=(
                f"All API keys for the **{agent_name}** agent have been exhausted.\n\n"
                "**What this means:**\n"
                f"• {agent_name} is now effectively paused\n"
                "• Other agents using the same key pool are also affected\n"
                "• Manual key rotation or wait-for-reset is required\n\n"
                "This alert is rate-limited (one per 6h). The fleet heartbeat "
                "will continue to show a **rate limited** status until keys recover."
            ),
            channel=AlertChannel.ALERTS,
            level=AlertLevel.CRITICAL,
            fields={
                "Agent": agent_name,
                "Action": "All keys exhausted — agent paused",
                "Environment": "VPS (no auto-rotation)",
            },
        )
        record_quota_alert(agent_name)
    except Exception:
        logger.exception(f"Failed to send quota-exhausted notification for {agent_name}")

T = TypeVar("T")


# ── Executor helpers ─────────────────────────────────────────────────────────


def run_in_executor(func: Callable[..., T]) -> Callable[..., Coroutine[Any, Any, T]]:
    """Decorator that runs a **synchronous** function in the default executor.

    Useful for offloading blocking I/O (e.g. FFmpeg, yt-dlp) from the
    async event loop.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    return wrapper


# ── Vault retry helper ───────────────────────────────────────────────────────


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def vault_op_with_retry(fn: Callable[[], Any]) -> Any:
    """Execute a synchronous Vault operation in a thread-pool with retry.

    Wraps any blocking ``vault.*`` call so it runs off the event loop and
    is retried up to 3 times with exponential back-off on any exception
    (typically network failures to HuggingFace / GCS).

    Usage::

        from atlas.vault import get_vault
        v = get_vault()
        await vault_op_with_retry(lambda: v.store_transcript(vid, data))
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn)
