"""Process-wide client-side pacing for paid STT providers (Grok/Mistral).

We never rely on the provider's own 429 alone: we pace our own calls so we
stay well under their per-minute quotas and never blow the daily budget.
Thread-safe and synchronous — these transcribers run inside
``asyncio.to_thread`` workers, so a threading lock is the right primitive.
"""

import threading
import time


class CallPacer:
    """Ensures a minimum spacing between successive calls, process-wide."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
