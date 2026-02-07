"""Maia Tracker: Video metrics monitoring agent."""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import aiohttp
from atlas.adapters.maia import MaiaDAO
from atlas.utils import HydraExecutor, KeyRing
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)


@task(name="fetch_targets")
async def fetch_targets_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch videos that need statistics updates."""
    dao = MaiaDAO()
    run_logger = get_run_logger()

    try:
        targets = await dao.fetch_tracker_targets(batch_size)
        run_logger.info(f"Fetched {len(targets)} videos for tracking (batch_size={batch_size}).")
        return targets
    except Exception as e:
        run_logger.error(f"Failed to fetch tracker targets: {e}")
        return []


@task(name="update_stats")
async def update_stats_task(videos: List[Dict[str, Any]], executor: HydraExecutor) -> int:
    """
    Fetch and update statistics for a batch of videos.

    Args:
        videos: List of video records from fetch_targets
        executor: HydraExecutor for API key rotation

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

    async def make_request(api_key: str) -> Dict[str, Any]:
        params["key"] = api_key

        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params=params) as resp:
                if resp.status == 200:
                    result: Dict[str, Any] = await resp.json()
                    return result
                elif resp.status in (403, 429):
                    error_text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {error_text[:200]}")
                else:
                    error_text = await resp.text()
                    run_logger.error(f"Tracker HTTP {resp.status}: {error_text[:200]}")
                    raise Exception(f"HTTP {resp.status}")

    try:
        response_json = await executor.execute_async(make_request)
    except SystemExit:
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
        stats_list = []
        for item in items:
            stats = item.get("statistics", {})
            stats_list.append(
                {
                    "video_id": item["id"],
                    "views": (int(stats.get("viewCount", 0)) if stats.get("viewCount") else None),
                    "likes": (int(stats.get("likeCount", 0)) if stats.get("likeCount") else None),
                    "comment_count": (
                        int(stats.get("commentCount", 0)) if stats.get("commentCount") else None
                    ),
                    "timestamp": datetime.now(timezone.utc),
                }
            )

        await dao.log_video_stats_batch(stats_list)
        await dao.update_video_stats_batch(items)

        run_logger.info(f"✓ Logged {len(stats_list)} stats to hot tier")
        return len(items)
    except Exception as e:
        run_logger.error(f"Failed to update stats in database: {e}")
        return 0


@flow(name="run_tracker_cycle")
async def tracker_flow(batch_size: int, executor: HydraExecutor) -> Dict[str, Any]:
    """
    Execute a complete Tracker cycle: fetch stale videos, update stats.

    Args:
        batch_size: Number of videos to process (max 50 for YouTube API)
        executor: HydraExecutor for API key rotation

    Returns:
        Dictionary with cycle statistics
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Tracker Cycle ===")

    stats: Dict[str, Any] = {
        "videos_fetched": 0,
        "videos_updated": 0,
        "updates_failed": 0,
    }

    try:
        if batch_size > 50:
            run_logger.warning(f"Batch size {batch_size} exceeds YouTube API limit. Capping at 50.")
            batch_size = 50

        targets = await fetch_targets_task(batch_size=batch_size)
        stats["videos_fetched"] = len(targets)

        if not targets:
            run_logger.info("No videos need tracking updates. Tracker cycle complete (idle).")
            return stats

        updated_count = await update_stats_task(targets, executor)
        stats["videos_updated"] = updated_count
        stats["updates_failed"] = len(targets) - updated_count

        run_logger.info(
            f"=== Tracker Cycle Complete === "
            f"Fetched: {stats['videos_fetched']}, "
            f"Updated: {stats['videos_updated']}, "
            f"Failed: {stats['updates_failed']}"
        )

    except SystemExit:
        run_logger.critical("Tracker Cycle terminated by Resiliency strategy (429 Rate Limit)")
        raise
    except Exception as e:
        run_logger.exception(f"Tracker cycle failed with unexpected error: {e}")
        raise

    return stats


class TrackerAgent:
    """
    Tracker Agent: Video metrics monitoring and statistics tracking.

    Implements the Agent protocol for polymorphic command dispatch.
    """

    name = "tracker"

    def __init__(self) -> None:
        """Initialize the Tracker agent with its KeyRing and executor."""
        self.logger = logging.getLogger(self.name)
        self.keys = KeyRing("tracking")
        self.executor = HydraExecutor(self.keys, agent_name="tracker")

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Tracker agent."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of videos to track per cycle (max 50, default: 50)",
        )

    async def run(self, batch_size: int = 50, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute a complete Tracker cycle.

        Args:
            batch_size: Number of videos to process (max 50 for YouTube API)
            **kwargs: Additional arguments (ignored)

        Returns:
            Dictionary with cycle statistics
        """
        return await tracker_flow(batch_size=batch_size, executor=self.executor)


@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50) -> Dict[str, Any]:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using TrackerAgent directly for new code.
    """
    agent = TrackerAgent()
    return await agent.run(batch_size=batch_size)


@task(name="fetch_targets")
async def fetch_targets(batch_size: int = 50) -> Any:
    """Legacy function wrapper for backward compatibility."""
    return await fetch_targets_task(batch_size)


@task(name="update_stats")
async def update_stats(videos: List[Dict[str, Any]]) -> int:
    """Legacy function wrapper for backward compatibility."""
    keys = KeyRing("tracking")
    executor = HydraExecutor(keys, agent_name="tracker")
    return await update_stats_task(videos, executor)


def main() -> None:
    """Entry point for running the Tracker as a standalone service."""
    try:
        agent = TrackerAgent()
        asyncio.run(agent.run())
    except SystemExit as e:
        logger.critical(f"Tracker terminated: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("Tracker stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Tracker failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
