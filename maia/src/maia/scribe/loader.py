"""Maia Scribe: Transcript loader using the shared YouTube streamer.

Thin wrapper around :meth:`StealthVideoStreamer.extract_captions` (yt-dlp native
subtitle extraction with TLS impersonation + Deno PoToken). This shares the exact
yt-dlp invocation the Painter uses, so there is a single YouTube interaction path.
"""

import logging
import tempfile
from typing import Any

from maia.media.streamer import (
    StealthVideoStreamer,
    TranscriptExtractionError,
    TranscriptRateLimitError,
)

logger = logging.getLogger(__name__)

# Caption cascade: YouTube throttles the `timedtext` endpoint per IP, but the
# different player clients resolve captions through semi-independent surfaces, so
# when one is rate-limited another often still succeeds. We try them in order and
# only surface a rate-limit error if *every* client is throttled.
_CAPTION_CLIENTS = ["default", "tv", "mweb"]


class TranscriptLoader:
    """Fetch video transcripts via the shared YouTube streamer."""

    def __init__(self, cookies_path: str | None = None) -> None:
        self.cookies_path = cookies_path
        self.logger = logging.getLogger("maia.scribe.loader")

    def _fetch_with_client(self, video_id: str, client: str) -> list[dict[str, Any]]:
        """Attempt caption extraction with a single player client.

        Raises:
            TranscriptRateLimitError: transient throttle — caller may try another client.
            TranscriptExtractionError: genuine "no subtitles" / failure for this client.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            return StealthVideoStreamer(self.cookies_path).extract_captions(
                video_id, client, tmpdir
            )

    def fetch(self, video_id: str) -> list[dict[str, Any]]:
        """Download captions for *video_id*, cascading across player clients.

        Tries each client in :data:`_CAPTION_CLIENTS`. A rate-limit on one client
        falls through to the next; only if **all** clients are throttled is a
        :class:`TranscriptRateLimitError` raised (so the flow re-queues the video).
        A genuine "no subtitles" result short-circuits the cascade.

        Returns:
            List of ``{"text", "start", "duration"}`` dicts (time in **seconds**).

        Raises:
            TranscriptExtractionError / TranscriptRateLimitError: see above.
        """
        last_rate_limit: TranscriptRateLimitError | None = None
        last_error: TranscriptExtractionError | None = None

        for client in _CAPTION_CLIENTS:
            try:
                return self._fetch_with_client(video_id, client)
            except TranscriptRateLimitError as e:
                self.logger.warning(
                    f"[yt-dlp] Rate-limited for {video_id} via '{client}', trying next client"
                )
                last_rate_limit = e
                continue
            except TranscriptExtractionError as e:
                # "No subtitles available" is authoritative — don't waste other clients.
                if "No subtitles available" in str(e):
                    raise
                last_error = e
                continue

        if last_rate_limit is not None:
            raise last_rate_limit
        if last_error is not None:
            raise last_error
        raise TranscriptExtractionError(f"All caption clients failed for {video_id}")
