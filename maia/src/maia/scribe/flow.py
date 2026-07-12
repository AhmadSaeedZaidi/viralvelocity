"""Maia Scribe: Transcript extraction agent.

Consumer in the Producer-Consumer pipeline. Pulls videos needing
transcripts from the video table, fetches them via yt-dlp native
subtitle extraction, and persists results to Atlas Vault.
"""

import argparse
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
MAX_CONCURRENT_TRANSCRIPTS = 2

# Pacing delay (seconds) between transcript fetches to stay under YouTube's per-IP rate limits.
SCRIBE_THROTTLE_SECONDS = 1.5


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
    run_logger = get_run_logger()
    vid_id = video.id

    # Idempotent: a DONE video is never re-transcribed, guarding manual reruns
    # from redundant STT/quota use (the claim gate already excludes it).
    if video.transcript_phase == "DONE":
        run_logger.info(f"Transcript already done for {vid_id} — skipping")
        return

    try:
        segments = await _transcribe(video)
        transcript_repo = TranscriptRepository()
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
    except TranscriptExtractionError as e:
        run_logger.warning(f"No transcript available for {vid_id}: {e}")
        await video_repo.mark_transcript_safe(vid_id)
    except Exception as e:
        run_logger.exception(f"Failed to scribe {vid_id} after retries: {e}")
        await video_repo.mark_failed(vid_id)


async def _transcribe(video: Video) -> list[dict[str, Any]]:
    """Return transcript segments, preferring vault artifacts over YouTube."""
    vid_id = video.id

    # This is the ONLY place that hits the `timedtext` endpoint, so the throttle
    # surface is centralized here.
    try:
        return TranscriptLoader().fetch(vid_id)
    except TranscriptExtractionError:
        pass  # no captions available — try audio STT below

    # Audio STT (paid Grok/Mistral fallback), gated by our own daily cap so we
    # never blow the budget (captions above are free and preferred).
    if audio_cap_reached():
        raise TranscriptExtractionError(
            f"Daily audio-transcription cap reached; skipping paid STT for {vid_id}"
        )
    try:
        audio_buf = await asyncio.to_thread(get_vault().fetch_binary, audio_path(vid_id))
        if audio_buf is not None:
            tmp = tempfile.mktemp(suffix=".opus")
            await asyncio.to_thread(Path(tmp).write_bytes, audio_buf.getvalue())
            segs = (await asyncio.to_thread(transcribe_audio_path, Path(tmp))).segments
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
    """Execute a complete Scribe cycle: fetch videos, transcribe, store to Vault.

    Args:
        batch_size: Number of videos to process.

    Returns a dict with cycle statistics.
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Scribe Cycle ===")

    targets = await fetch_scribe_targets_task(batch_size)

    if not targets:
        run_logger.info("No videos need transcripts. Scribe cycle complete (idle).")
        return {"videos_processed": 0}

    run_logger.info(f"Processing {len(targets)} videos concurrently...")

    sem = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTS)

    async def _bounded(video: Video) -> None:
        async with sem:
            await process_transcript_task(video)
            await asyncio.sleep(SCRIBE_THROTTLE_SECONDS)

    results = await asyncio.gather(*[_bounded(v) for v in targets], return_exceptions=True)
    # Propagate any QuotaExhaustedError that was caught by return_exceptions
    quota_errors = [r for r in results if isinstance(r, QuotaExhaustedError)]
    if quota_errors:
        raise quota_errors[0]

    # Cycle completed without quota exhaustion — clear any stale marker.
    clear_quota_exhausted("scribe")

    run_logger.info(f"=== Scribe Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class ScribeAgent:
    """Scribe Agent: transcript extraction and storage."""

    name = "scribe"

    def __init__(self) -> None:
        """Initialize the Scribe agent."""
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Scribe agent."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of videos to process per cycle (default: 10)",
        )

    async def run(self, batch_size: int = 10, **kwargs: Any) -> dict[str, Any]:
        """Execute a complete Scribe cycle and return its statistics dict."""
        result: dict[str, Any] = await scribe_flow(batch_size=batch_size)
        return result


@task(name="process_transcript")
async def process_transcript(video: dict[str, Any]) -> None:
    """Legacy Task wrapper — converts dict to Video and delegates."""
    from atlas.models import Video as VideoModel

    await process_transcript_task(VideoModel(**video))


@flow(name="run_scribe_cycle")
async def run_scribe_cycle(batch_size: int = 10) -> None:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using ScribeAgent directly for new code.
    """
    agent = ScribeAgent()
    await agent.run(batch_size=batch_size)


def main() -> None:
    """Entry point for running the Scribe as a standalone service."""
    try:
        agent = ScribeAgent()
        asyncio.run(agent.run())
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
