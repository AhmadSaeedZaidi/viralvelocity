"""Maia Tracker: Video metrics monitoring agent.

Consumer in the Producer-Consumer pipeline. Pulls due videos from the
adaptive-scheduling ``watchlist``, fetches fresh statistics from the YouTube
Data API, persists them to ``video_stats_log``, and advances each video's
decay tier (age + view velocity).
"""

import argparse
import logging
from datetime import UTC, datetime
from typing import Any

from atlas.notifications import AlertChannel, AlertLevel, notifier
from atlas.repositories import VideoRepository, WatchlistRepository
from atlas.state import clear_quota_exhausted
from atlas.utils import QuotaExhaustedError
from prefect import flow, get_run_logger, task

from maia.strategies import YouTubeSearchStrategy
from maia.utils import cli_bootstrap, notify_quota_exhausted, run_agent_main

logger = logging.getLogger(__name__)


def _video_id(v: dict[str, Any]) -> str:
    """Extract the id from a watchlist item dict (``video_id`` or legacy ``id``)."""
    return v.get("video_id") or v["id"]


@task(name="fetch_targets")
async def fetch_targets_task(batch_size: int) -> list[dict[str, Any]]:
    """Fetch videos that need statistics updates (due per adaptive schedule)."""
    watchlist_repo = WatchlistRepository()
    run_logger = get_run_logger()

    try:
        targets = await watchlist_repo.fetch_batch(batch_size)
        run_logger.info(f"Fetched {len(targets)} videos from watchlist (batch_size={batch_size}).")
        return [t.model_dump() for t in targets]
    except Exception as e:
        run_logger.exception(f"Failed to fetch tracker targets: {e}")
        return []


@task(name="update_stats")
async def update_stats_task(videos: list[dict[str, Any]], strategy: YouTubeSearchStrategy) -> int:
    """Fetch and update statistics for a batch of videos.

    Args:
        videos: List of watchlist items (dicts) due for tracking.
        strategy: YouTubeSearchStrategy for API access.

    Returns the number of videos successfully updated.
    """
    if not videos:
        return 0

    run_logger = get_run_logger()
    video_repo = VideoRepository()
    watchlist_repo = WatchlistRepository()

    video_ids = [_video_id(v) for v in videos]

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

    returned_ids = {item["id"] for item in items}
    missing_ids = [_video_id(v) for v in videos if _video_id(v) not in returned_ids]

    # Advance every sampled video's decay schedule: live videos get a new tier
    # from age + view velocity; missing videos (deleted/private/geo-blocked)
    # rotate to a less frequent tier so they don't wedge the queue.
    try:
        if items:
            await video_repo.update_stats_batch(items)

        velocities = await watchlist_repo.velocity_views_per_hour(video_ids)
        updates: list[dict[str, Any]] = []
        for v in videos:
            tier, next_track_at = watchlist_repo.calculate_next_track_time(
                published_at=v.get("published_at"),
                views_per_hour=velocities.get(_video_id(v)),
                tier=v.get("tracking_tier"),
            )
            updates.append(
                {
                    "video_id": _video_id(v),
                    "tracking_tier": tier,
                    "last_tracked_at": datetime.now(UTC),
                    "next_track_at": next_track_at,
                }
            )
        await watchlist_repo.update_schedule(updates)

        if missing_ids:
            run_logger.info(
                f"Advanced {len(missing_ids)} videos not on YouTube "
                "(deleted/private/geo-blocked) toward less-frequent tracking."
            )
        if items:
            run_logger.info(f"✓ Logged {len(items)} stats to hot tier")
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

        snapshot = await VideoRepository().pipeline_snapshot()
        status_str = ", ".join(f"{k}: {v}" for k, v in sorted(snapshot.get("status_counts", {}).items()))
        await notifier.send(
            title="Tracker Cycle Summary",
            description=(
                f"Updated: {stats['videos_updated']}/{stats['videos_fetched']} | "
                f"Not on YouTube: {stats['updates_failed']}"
            ),
            channel=AlertChannel.SURVEILLANCE,
            level=AlertLevel.SUCCESS if stats['updates_failed'] == 0 else AlertLevel.WARNING,
            fields={
                "Videos Updated": str(stats["videos_updated"]),
                "Video Stats": status_str,
                "Total Corpus": str(snapshot.get("total", 0)),
                "Transcripts": str(snapshot.get("transcripts", 0)),
                "Audios": str(snapshot.get("audios", 0)),
                "Visuals": str(snapshot.get("with_visuals", 0)),
                "Ingested (1h)": str(snapshot.get("ingested_1h", 0)),
            },
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


@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50) -> dict[str, Any]:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using TrackerAgent directly for new code.
    """
    agent = TrackerAgent()
    return await agent.run(batch_size=batch_size)


def main() -> None:
    run_agent_main(TrackerAgent().run, "tracker")


if __name__ == "__main__":
    cli_bootstrap()
    main()
