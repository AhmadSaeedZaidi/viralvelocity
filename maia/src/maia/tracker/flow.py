"""Maia Tracker: Video metrics monitoring agent.

Consumer in the Producer-Consumer pipeline. Pulls stale videos from
the video table, fetches fresh statistics from the YouTube Data API,
and persists them back to Atlas.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from atlas.models import VideoStats
from atlas.repositories import VideoRepository
from atlas.state import clear_quota_exhausted
from atlas.utils import QuotaExhaustedError
from prefect import flow, get_run_logger, task

from maia.strategies import YouTubeSearchStrategy
from maia.utils import notify_quota_exhausted

logger = logging.getLogger(__name__)


@task(name="fetch_targets")
async def fetch_targets_task(batch_size: int) -> list[dict[str, Any]]:
    """Fetch videos that need statistics updates."""
    video_repo = VideoRepository()
    run_logger = get_run_logger()

    try:
        targets = await video_repo.fetch_tracker_targets(batch_size)
        run_logger.info(f"Fetched {len(targets)} videos for tracking (batch_size={batch_size}).")
        return [t.model_dump() for t in targets]
    except Exception as e:
        run_logger.exception(f"Failed to fetch tracker targets: {e}")
        return []


@task(name="update_stats")
async def update_stats_task(videos: list[dict[str, Any]], strategy: YouTubeSearchStrategy) -> int:
    """Fetch and update statistics for a batch of videos.

    Args:
        videos: List of video records from fetch_targets.
        strategy: YouTubeSearchStrategy for API access.

    Returns the number of videos successfully updated.
    """
    if not videos:
        return 0

    run_logger = get_run_logger()
    video_repo = VideoRepository()

    video_ids = [v["id"] for v in videos]

    run_logger.info(f"Fetching stats for {len(video_ids)} videos...")

    try:
        response_json = await strategy.fetch_videos(video_ids)
    except QuotaExhaustedError:
        await notify_quota_exhausted("tracker")
        raise
    except Exception as e:
        run_logger.exception(f"Failed to fetch stats: {e}")
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
                VideoStats(
                    video_id=item["id"],
                    views=(int(stats.get("viewCount", 0)) if stats.get("viewCount") else None),
                    likes=(int(stats.get("likeCount", 0)) if stats.get("likeCount") else None),
                    comment_count=(
                        int(stats.get("commentCount", 0)) if stats.get("commentCount") else None
                    ),
                    timestamp=datetime.now(UTC),
                )
            )

        await video_repo.log_stats_batch(stats_list)
        await video_repo.update_stats_batch(items)

        run_logger.info(f"✓ Logged {len(stats_list)} stats to hot tier")
        return len(items)
    except Exception as e:
        run_logger.exception(f"Failed to update stats in database: {e}")
        return 0


@flow(name="run_tracker_cycle")
async def tracker_flow(batch_size: int, strategy: YouTubeSearchStrategy) -> dict[str, Any]:
    """Execute a complete Tracker cycle: fetch stale videos, update stats.

    Args:
        batch_size: Number of videos to process (max 50 for YouTube API).
        strategy: YouTubeSearchStrategy for API access.

    Returns a dict with cycle statistics.
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Tracker Cycle ===")

    stats: dict[str, Any] = {
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
            clear_quota_exhausted("tracker")
            return stats

        updated_count = await update_stats_task(targets, strategy)
        stats["videos_updated"] = updated_count
        stats["updates_failed"] = len(targets) - updated_count

        run_logger.info(
            f"=== Tracker Cycle Complete === "
            f"Fetched: {stats['videos_fetched']}, "
            f"Updated: {stats['videos_updated']}, "
            f"Failed: {stats['updates_failed']}"
        )

    except QuotaExhaustedError:
        run_logger.critical("Tracker Cycle terminated — all API keys exhausted")
        raise
    except Exception as e:
        run_logger.exception(f"Tracker cycle failed with unexpected error: {e}")
        raise

    clear_quota_exhausted("tracker")
    return stats


class TrackerAgent:
    """Tracker Agent: video metrics monitoring and statistics tracking."""

    name = "tracker"

    def __init__(self) -> None:
        """Initialize the Tracker agent with its YouTube search strategy."""
        self.logger = logging.getLogger(self.name)
        self.strategy = YouTubeSearchStrategy("tracking", agent_name="tracker")

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Tracker agent."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of videos to track per cycle (max 50, default: 50)",
        )

    async def run(self, batch_size: int = 50, **kwargs: Any) -> dict[str, Any]:
        """Execute a complete Tracker cycle and return its statistics dict."""
        result: dict[str, Any] = await tracker_flow(batch_size=batch_size, strategy=self.strategy)
        return result


@task(name="update_stats")
async def update_stats(videos: list[dict[str, Any]]) -> int:
    """Legacy Task wrapper — creates strategy and delegates."""
    strategy = YouTubeSearchStrategy("tracking", agent_name="legacy_tracker")
    result: int = await update_stats_task(videos, strategy)
    return result


@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50) -> dict[str, Any]:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using TrackerAgent directly for new code.
    """
    agent = TrackerAgent()
    return await agent.run(batch_size=batch_size)


def main() -> None:
    """Entry point for running the Tracker as a standalone service."""
    try:
        agent = TrackerAgent()
        asyncio.run(agent.run())
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
