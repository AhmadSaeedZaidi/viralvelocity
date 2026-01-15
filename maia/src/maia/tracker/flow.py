"""Maia Tracker: Video metrics monitoring agent."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from prefect import flow, get_run_logger, task

from atlas.adapters.maia import MaiaDAO
from atlas.utils import HydraExecutor, KeyRing

logger = logging.getLogger(__name__)

tracker_keys = KeyRing("tracking")
tracker_executor = HydraExecutor(tracker_keys, agent_name="tracker")


@task(name="fetch_targets")
async def fetch_targets(batch_size: int = 50) -> List[Dict[str, Any]]:
    dao = MaiaDAO()
    logger = get_run_logger()

    try:
        targets = await dao.fetch_tracker_targets(batch_size)
        logger.info(
            f"Fetched {len(targets)} videos for tracking (batch_size={batch_size})."
        )
        return targets
    except Exception as e:
        logger.error(f"Failed to fetch tracker targets: {e}")
        return []


@task(name="update_stats")
async def update_stats(videos: List[Dict[str, Any]]) -> int:
    """
    Fetch and update statistics for a batch of videos.

    Args:
        videos: List of video records from fetch_targets

    Returns:
        Number of videos successfully updated
    """
    if not videos:
        return 0

    run_logger = get_run_logger()
    dao = MaiaDAO()

    video_ids = [v["id"] for v in videos]
    id_str = ",".join(video_ids)

    run_logger.info(f"Fetching stats for {len(video_ids)} videos...")

    base_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "statistics",
        "id": id_str,
    }

    # Define the request function for HydraExecutor
    async def make_request(api_key: str) -> Dict[str, Any]:
        params["key"] = api_key

        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status in (403, 429):
                    # Raise exception for Hydra Protocol handling
                    error_text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {error_text[:200]}")
                else:
                    error_text = await resp.text()
                    run_logger.error(f"Tracker HTTP {resp.status}: {error_text[:200]}")
                    raise Exception(f"HTTP {resp.status}")

    # Execute with HydraExecutor (handles key rotation and termination)
    try:
        response_json = await tracker_executor.execute_async(make_request)
    except SystemExit:
        # Hydra Protocol - propagate clean termination
        raise
    except Exception as e:
        run_logger.error(f"Failed to fetch stats: {e}")
        return 0

    if not response_json:
        run_logger.error("No response from API")
        return 0

    items = response_json.get("items", [])
    if not items:
        run_logger.warning("API returned no items (videos may be deleted/private).")
        return 0

    try:
        # Write to hot tier (video_stats_log table)
        from datetime import datetime, timezone

        stats_list = []
        for item in items:
            stats = item.get("statistics", {})
            stats_list.append(
                {
                    "video_id": item["id"],
                    "views": (
                        int(stats.get("viewCount", 0))
                        if stats.get("viewCount")
                        else None
                    ),
                    "likes": (
                        int(stats.get("likeCount", 0))
                        if stats.get("likeCount")
                        else None
                    ),
                    "comment_count": (
                        int(stats.get("commentCount", 0))
                        if stats.get("commentCount")
                        else None
                    ),
                    "timestamp": datetime.now(timezone.utc),
                }
            )

        # Log to hot tier
        await dao.log_video_stats_batch(stats_list)

        # Also update last_updated_at on videos table (legacy behavior)
        await dao.update_video_stats_batch(items)

        run_logger.info(f"✓ Logged {len(stats_list)} stats to hot tier")
        return len(items)
    except Exception as e:
        run_logger.error(f"Failed to update stats in database: {e}")
        return 0


@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50) -> Dict[str, Any]:
    """
    Execute a complete Tracker cycle: fetch stale videos, update stats.

    Args:
        batch_size: Number of videos to process (max 50 for YouTube API)

    Returns:
        Dictionary with cycle statistics
    """
    logger = get_run_logger()
    logger.info("=== Starting Tracker Cycle ===")

    stats = {
        "videos_fetched": 0,
        "videos_updated": 0,
        "updates_failed": 0,
    }

    try:
        # Enforce YouTube API batch limit
        if batch_size > 50:
            logger.warning(
                f"Batch size {batch_size} exceeds YouTube API limit. Capping at 50."
            )
            batch_size = 50

        targets = await fetch_targets(batch_size=batch_size)
        stats["videos_fetched"] = len(targets)

        if not targets:
            logger.info(
                "No videos need tracking updates. Tracker cycle complete (idle)."
            )
            return stats

        updated_count = await update_stats(targets)
        stats["videos_updated"] = updated_count
        stats["updates_failed"] = len(targets) - updated_count

        logger.info(
            f"=== Tracker Cycle Complete === "
            f"Fetched: {stats['videos_fetched']}, "
            f"Updated: {stats['videos_updated']}, "
            f"Failed: {stats['updates_failed']}"
        )

    except SystemExit:
        # Hydra Protocol: Rate limit detected - propagate immediately
        logger.critical("Tracker Cycle terminated by Hydra Protocol (429 Rate Limit)")
        raise
    except Exception as e:
        logger.exception(f"Tracker cycle failed with unexpected error: {e}")
        raise

    return stats


def main() -> None:
    """Entry point for running the Tracker as a standalone service."""
    try:
        asyncio.run(run_tracker_cycle())
    except SystemExit as e:
        # Hydra Protocol: Exit with specific code for rate limit
        logger.critical(f"Tracker terminated: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("Tracker stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Tracker failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
