"""Shared utilities for Maia agents.

Centralises helpers that were previously duplicated across agent modules
(retry wrappers, common exceptions).
"""

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

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


async def notify_quota_exhausted(agent_name: str) -> None:
    """Send a Discord alert that all API keys are exhausted for *agent_name*.

    Rate-limited to one per cooldown window per agent (so the resiliency
    restart/retry loop does not spam Discord); the exhausted state is also
    recorded so the heartbeat can show a "rate limited" status.
    """
    from atlas.state import (
        mark_quota_exhausted,
        record_quota_alert,
        should_send_quota_alert,
    )

    mark_quota_exhausted(agent_name)
    if not should_send_quota_alert(agent_name):
        logger.info(f"Quota-exhausted alert for {agent_name} suppressed (within cooldown).")
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def vault_op_with_retry(fn: Callable[[], Any]) -> Any:
    """Run a blocking Vault operation in a thread-pool, retrying on failure.

    Retries up to 3 times with exponential back-off (typically network
    failures to HuggingFace / GCS).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn)
