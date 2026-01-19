import asyncio
import json
import logging
from typing import Any, Dict, List

from prefect import flow, get_run_logger, task
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from atlas.adapters.maia import MaiaDAO
from atlas.vault import vault

from .loader import TranscriptLoader

logger = logging.getLogger("maia.scribe")


@task(name="fetch_scribe_targets")  # type: ignore[misc]
async def fetch_scribe_targets(batch_size: int = 10) -> Any:
    dao = MaiaDAO()
    targets = await dao.fetch_scribe_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing transcripts.")
    return targets


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


@task(name="process_transcript")
async def process_transcript(video: Dict[str, Any]) -> None:
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]
    loader = TranscriptLoader()

    try:
        # Fetch transcript with retry
        transcript_data = await _fetch_transcript_with_retry(loader, vid_id)

        if transcript_data:
            # Store full JSON structure in the Vault with retry
            await _store_to_vault_with_retry(vid_id, transcript_data)

            # Mark as safe in DB (data is in vault)
            await dao.mark_video_transcript_safe(vid_id)
            run_logger.info(f"Scribed transcript for {vid_id}")
        else:
            # Mark unavailable (TranscriptsDisabled or not found)
            run_logger.warning(f"Transcripts unavailable/disabled for {vid_id}")
            # Still mark as processed to avoid retrying
            await dao.mark_video_transcript_safe(vid_id)

    except SystemExit:
        # Re-raise SystemExit to ensure the Hydra Protocol triggers
        raise
    except Exception as e:
        run_logger.error(f"Failed to scribe {vid_id} after retries: {e}")
        await dao.mark_video_failed(vid_id)


@flow(name="run_scribe_cycle")
async def run_scribe_cycle(batch_size: int = 10) -> None:
    """
    Execute a complete Scribe cycle: fetch videos, download transcripts, store to Vault.

    Args:
        batch_size: Number of videos to process (default: 10)

    Returns:
        None
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Scribe Cycle ===")

    targets = await fetch_scribe_targets(batch_size)

    if not targets:
        run_logger.info("No videos need transcripts. Scribe cycle complete (idle).")
        return

    run_logger.info(f"Processing {len(targets)} videos...")

    # Process sequentially to manage rate limits more gently
    for video in targets:
        await process_transcript(video)

    run_logger.info(f"=== Scribe Cycle Complete === Processed {len(targets)} videos")


def main() -> None:
    """Entry point for running the Scribe as a standalone service."""
    try:
        asyncio.run(run_scribe_cycle())  # type: ignore[arg-type]
    except KeyboardInterrupt:
        logger.info("Scribe stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Scribe failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
