"""Maia Scribe: Transcript extraction agent.

Consumer in the Producer-Consumer pipeline. Pulls videos needing
transcripts from the video table, fetches them via youtube-transcript-api,
and persists results to Atlas Vault.
"""

import argparse
import asyncio
import logging
from typing import Any, Dict, List

from atlas.models import Video
from atlas.repositories import VideoRepository
from atlas.vault import get_vault
from prefect import flow, get_run_logger, task
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Re-export TranscriptsDisabled so the flow can catch it
from youtube_transcript_api import TranscriptsDisabled  # type: ignore[attr-defined]

from maia.utils import RateLimitError, vault_op_with_retry

from .loader import TranscriptExtractionError, TranscriptLoader

logger = logging.getLogger(__name__)

# Concurrent transcripts — bounded by semaphore to avoid overwhelming external APIs
MAX_CONCURRENT_TRANSCRIPTS = 5


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _fetch_transcript_with_retry(loader: TranscriptLoader, vid_id: str) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, loader.fetch, vid_id)


@task(name="fetch_scribe_targets")
async def fetch_scribe_targets_task(batch_size: int) -> List[Video]:
    """Fetch videos that need transcripts."""
    video_repo = VideoRepository()
    targets = await video_repo.claim_scribe_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing transcripts.")
    return targets


@task(name="process_transcript")
async def process_transcript_task(video: Video) -> None:
    """Process a single video's transcript."""
    video_repo = VideoRepository()
    run_logger = get_run_logger()
    vid_id = video.id
    loader = TranscriptLoader()

    try:
        transcript_data = await _fetch_transcript_with_retry(loader, vid_id)

        v = get_vault()
        await vault_op_with_retry(lambda: v.store_transcript(vid_id, transcript_data))
        await video_repo.mark_transcript_safe(vid_id)
        run_logger.info(f"Scribed transcript for {vid_id}")

    except RateLimitError:
        run_logger.critical(f"Rate limit hit while scribing {vid_id}. Propagating.")
        raise
    except TranscriptsDisabled:
        run_logger.warning(f"Transcripts disabled for {vid_id}")
        await video_repo.mark_transcript_safe(vid_id)
    except Exception as e:
        run_logger.error(f"Failed to scribe {vid_id} after retries: {e}")
        await video_repo.mark_failed(vid_id)


@flow(name="run_scribe_cycle")
async def scribe_flow(batch_size: int) -> Dict[str, Any]:
    """
    Execute a complete Scribe cycle: fetch videos, download transcripts, store to Vault.

    Args:
        batch_size: Number of videos to process

    Returns:
        Dictionary with cycle statistics
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

    await asyncio.gather(*[_bounded(v) for v in targets], return_exceptions=True)

    run_logger.info(f"=== Scribe Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class ScribeAgent:
    """
    Scribe Agent: Transcript extraction and storage.

    Implements the Agent protocol for polymorphic command dispatch.
    """

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

    async def run(self, batch_size: int = 10, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute a complete Scribe cycle.

        Args:
            batch_size: Number of videos to process
            **kwargs: Additional arguments (ignored)

        Returns:
            Dictionary with cycle statistics
        """
        result: Dict[str, Any] = await scribe_flow(batch_size=batch_size)
        return result


@task(name="process_transcript")
async def process_transcript(video: Dict[str, Any]) -> None:
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
