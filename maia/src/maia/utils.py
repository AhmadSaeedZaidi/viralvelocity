"""Shared utilities for Maia agents.

Centralises helpers that were previously duplicated across agent modules
(retry wrappers, executor helpers, common exceptions).
"""

import asyncio
import functools
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from atlas.utils import ResiliencyExecutor
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Shared exceptions ────────────────────────────────────────────────────────


class RateLimitError(Exception):
    """HTTP 429 rate-limit exceeded."""


# ── Boundary wrapper ─────────────────────────────────────────────────────────


async def execute_with_rate_limit(
    executor: ResiliencyExecutor,
    request_func: Callable[[str], Any],
    error_classifier: Callable[[Exception], tuple[bool, bool]] | None = None,
) -> Any | None:
    """Execute an async request via ResiliencyExecutor, converting SystemExit to RateLimitError.

    Atlas's ResiliencyExecutor calls ``sys.exit(0)`` when all API keys are
    exhausted (the "Hydra Protocol" — container suicide for IP rotation).
    ``SystemExit`` is a ``BaseException`` that crashes ``asyncio.gather``
    and bypasses normal ``except Exception`` handlers.

    This boundary wrapper catches ``SystemExit`` at the maia layer and
    re-raises it as ``RateLimitError(Exception)`` so it propagates cleanly
    through async task groups and Prefect flows.
    """
    try:
        return await executor.execute_async(request_func, error_classifier)
    except SystemExit:
        raise RateLimitError(
            f"All API keys exhausted for {executor.agent_name} — rate-limited (429)"
        )


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
