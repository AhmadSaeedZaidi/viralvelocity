"""Maia Scribe: Transcript extraction agent.

Consumer in the Producer-Consumer pipeline. Pulls videos needing
transcripts from the video table, fetches them via yt-dlp native
subtitle extraction, and persists results to Atlas Vault.
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from atlas.models import Video
from atlas.repositories import TranscriptRepository, VideoRepository
from atlas.state import audio_cap_reached, clear_quota_exhausted, record_audio_usage
from atlas.utils import QuotaExhaustedError
from atlas.vault import audio_path, get_vault
from prefect import flow, get_run_logger, task

from maia.base import BaseBatchAgent
from maia.utils import notify_quota_exhausted

from .loader import (
    TranscriptExtractionError,
    TranscriptLoader,
    TranscriptRateLimitError,
)
from .transcription import (
    transcribe_audio_download,
    transcribe_audio_path,
)

logger = logging.getLogger(__name__)

# Kept low because the VPS egress IP is flagged by YouTube; high concurrency triggers HTTP 429.
MAX_CONCURRENT_TRANSCRIPTS = 1

# Pacing delay (seconds) between transcript fetches to stay under YouTube's per-IP rate limits.
SCRIBE_THROTTLE_SECONDS = 1.5

# Scribe will not spend paid STT hours on videos longer than this; instead it
# writes a templated "unavailable" transcript. YouTube captions (free) are still
# used when present, regardless of length.
SCRIBE_MAX_DURATION_SECONDS = 1800

SCRIBE_LONG_VIDEO_MESSAGE = "error: sorry, video too long and auto transcript not available"


class TranscriptTooLongError(Exception):
    """Raised when a video is too long for the paid STT fallback (no auto transcript)."""


@task(name="fetch_scribe_targets")
async def fetch_scribe_targets_task(batch_size: int) -> list[Video]:
    """Fetch videos that need transcripts."""
    video_repo = VideoRepository()
    targets = await video_repo.claim_scribe_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing transcripts.")
    return targets  # type: ignore[no-any-return]


@task(name="process_transcript")
async def process_transcript_task(video: Video) -> None:
    """Transcribe a video and stage it locally for the janitor to persist.

    Idempotent on DONE. Releases to PENDING on rate-limit (for retry) and marks
    the video safe when no transcript is available.
    """
    video_repo = VideoRepository()
    transcript_repo = TranscriptRepository()
    run_logger = get_run_logger()
    vid_id = video.id

    # Idempotent: a DONE video is never re-transcribed, guarding manual reruns
    # from redundant STT/quota use (the claim gate already excludes it).
    if video.transcript_phase == "DONE":
        run_logger.info(f"Transcript already done for {vid_id} — skipping")
        return

    try:
        segments = await _transcribe(video)
        await transcript_repo.record_transcript(
            vid_id,
            vault_uri=None,
            language="en",
            content_json=segments,
        )
        await video_repo.mark_transcript_safe(vid_id)
        run_logger.info(f"Scribed transcript for {vid_id}")

    except QuotaExhaustedError:
        await notify_quota_exhausted("scribe")
        raise
    except TranscriptRateLimitError as e:
        # Transient — release back to PENDING so it retries on a later cycle
        # instead of being permanently marked as having no transcript.
        run_logger.warning(f"Rate-limited on {vid_id}, releasing for retry: {e}")
        await video_repo.release_to_pending(vid_id)
    except TranscriptTooLongError:
        # Long video with no auto transcript: store a templated notice so the
        # corpus (and the janitor's vault flush) has a record, without spending
        # paid STT hours or raising concerning errors.
        run_logger.info(f"Too long for auto transcript; templated notice for {vid_id}")
        await transcript_repo.record_transcript(
            vid_id,
            vault_uri=None,
            language="en",
            content_json=[{"text": SCRIBE_LONG_VIDEO_MESSAGE}],
        )
        await video_repo.mark_transcript_safe(vid_id)
    except TranscriptExtractionError as e:
        run_logger.warning(f"No transcript available for {vid_id}: {e}")
        await video_repo.mark_transcript_safe(vid_id)
    except Exception as e:
        run_logger.exception(f"Failed to scribe {vid_id} after retries: {e}")
        await video_repo.mark_failed(vid_id)


async def _transcribe(video: Video) -> list[dict[str, Any]]:
    """Return transcript segments, preferring YouTube captions over paid STT."""
    vid_id = video.id
    duration = getattr(video, "duration", None)
    too_long = bool(duration and duration > SCRIBE_MAX_DURATION_SECONDS)

    # Prefer free YouTube captions; the caption throttle surface is centralized
    # here. Any loader failure (including live/unavailable videos that raise
    # non-TranscriptExtractionError types) means "no captions". Quota exhaustion
    # is a real signal and must propagate to the caller.
    try:
        return TranscriptLoader().fetch(vid_id)
    except QuotaExhaustedError:
        raise
    except Exception as e:  # noqa: BLE001 - any other failure means no captions
        logger.info(f"No YouTube captions for {vid_id}: {e}")

    # No auto transcript available. Long videos skip the paid STT fallback so we
    # never burn STT budget (or spam errors) on multi-hour broadcasts. The caller
    # writes a templated "unavailable" transcript for these.
    if too_long:
        raise TranscriptTooLongError(
            f"No auto transcript and video too long ({duration}s) for {vid_id}"
        )

    # Audio STT (paid Grok/Mistral fallback), gated by our own daily cap so we
    # never blow the budget (captions above are free and preferred).
    if audio_cap_reached():
        raise TranscriptExtractionError(
            f"Daily audio-transcription cap reached; skipping paid STT for {vid_id}"
        )
    try:
        audio_buf = await asyncio.to_thread(get_vault().fetch_binary, audio_path(vid_id))
        if audio_buf is not None:
            with tempfile.NamedTemporaryFile(suffix=".opus", delete=False) as tmp:
                tmp.write(audio_buf.getvalue())
                tmp_path = Path(tmp.name)
            segs = (await asyncio.to_thread(transcribe_audio_path, tmp_path)).segments
        else:
            segs = (await asyncio.to_thread(transcribe_audio_download, vid_id)).segments
        record_audio_usage(1)
        return segs
    except (TranscriptExtractionError, QuotaExhaustedError):
        raise
    except Exception as e:
        raise TranscriptExtractionError(f"Audio STT unavailable for {vid_id}: {e}") from e


@flow(name="run_scribe_cycle")
async def scribe_flow(batch_size: int) -> dict[str, Any]:
    """Execute a complete Scribe cycle: fetch videos, transcribe, store to Vault."""
    return await ScribeAgent().run(batch_size=batch_size)


class ScribeAgent(BaseBatchAgent):
    """Scribe Agent: transcript extraction and storage."""

    name = "scribe"
    default_batch_size = 10
    max_concurrent = MAX_CONCURRENT_TRANSCRIPTS
    throttle_seconds = SCRIBE_THROTTLE_SECONDS
    raise_on = (QuotaExhaustedError,)

    async def claim_batch(self, n: int) -> list[Video]:
        return await fetch_scribe_targets_task(n)

    async def process_one(self, video: Video) -> None:
        await process_transcript_task(video)

    async def after_cycle(self) -> None:
        # Cycle completed without quota exhaustion — clear any stale marker so the
        # heartbeat stops reporting scribe as rate-limited.
        clear_quota_exhausted("scribe")


@flow(name="run_scribe_cycle")
async def run_scribe_cycle(batch_size: int = 10) -> None:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using ScribeAgent directly for new code.
    """
    await ScribeAgent().run(batch_size=batch_size)


def main() -> None:
    """Entry point for running the Scribe as a standalone service."""
    try:
        agent = ScribeAgent()
        asyncio.run(scribe_flow(batch_size=agent.default_batch_size))
    except KeyboardInterrupt:
        logger.info("Scribe stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Scribe failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
