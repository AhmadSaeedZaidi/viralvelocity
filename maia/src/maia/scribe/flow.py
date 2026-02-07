"""Maia Scribe: Transcript extraction agent."""

import argparse
import asyncio
import logging
from typing import Any, Dict, List

from atlas.adapters.maia import MaiaDAO
from atlas.vault import vault
from prefect import flow, get_run_logger, task
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .loader import TranscriptLoader

logger = logging.getLogger("maia.scribe")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _fetch_transcript_with_retry(loader: TranscriptLoader, vid_id: str) -> Any:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, loader.fetch, vid_id)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _store_to_vault_with_retry(vid_id: str, transcript_data: Any) -> None:
    """Store transcript to vault with retry logic for network failures."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: vault.store_transcript(vid_id, transcript_data))


@task(name="fetch_scribe_targets")
async def fetch_scribe_targets_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch videos that need transcripts."""
    dao = MaiaDAO()
    targets = await dao.fetch_scribe_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing transcripts.")
    return targets


@task(name="process_transcript")
async def process_transcript_task(video: Dict[str, Any]) -> None:
    """Process a single video's transcript."""
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]
    loader = TranscriptLoader()

    try:
        transcript_data = await _fetch_transcript_with_retry(loader, vid_id)

        if transcript_data:
            await _store_to_vault_with_retry(vid_id, transcript_data)
            await dao.mark_video_transcript_safe(vid_id)
            run_logger.info(f"Scribed transcript for {vid_id}")
        else:
            run_logger.warning(f"Transcripts unavailable/disabled for {vid_id}")
            await dao.mark_video_transcript_safe(vid_id)

    except SystemExit:
        raise
    except Exception as e:
        run_logger.error(f"Failed to scribe {vid_id} after retries: {e}")
        await dao.mark_video_failed(vid_id)


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

    run_logger.info(f"Processing {len(targets)} videos...")

    for video in targets:
        await process_transcript_task(video)

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
        return await scribe_flow(batch_size=batch_size)


@flow(name="run_scribe_cycle")
async def run_scribe_cycle(batch_size: int = 10) -> None:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using ScribeAgent directly for new code.
    """
    agent = ScribeAgent()
    await agent.run(batch_size=batch_size)


@task(name="fetch_scribe_targets")
async def fetch_scribe_targets(batch_size: int = 10) -> Any:
    """Legacy function wrapper for backward compatibility."""
    return await fetch_scribe_targets_task(batch_size)


@task(name="process_transcript")
async def process_transcript(video: Dict[str, Any]) -> None:
    """Legacy function wrapper for backward compatibility."""
    await process_transcript_task(video)


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
