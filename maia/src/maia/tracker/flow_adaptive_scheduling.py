"""
Maia Tracker - Adaptive Scheduling Implementation

Monitors viral velocity using adaptive tracking schedules.
Operates independently of the videos table - tracks videos indefinitely
even after they've been archived from tiered storage.

Key Features:
- Uses watchlist table instead of videos table
- Stores metrics in Vault (Parquet) instead of SQL
- Adaptive tracking tiers based on video age
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from atlas.repositories import WatchlistRepository
from atlas.utils import KeyRing, ResiliencyExecutor
from atlas.vault import get_vault
from prefect import flow, get_run_logger, task

from maia.utils import RateLimitError, execute_with_rate_limit

logger = logging.getLogger(__name__)

tracker_keys = KeyRing("tracking")
tracker_executor = ResiliencyExecutor(tracker_keys, agent_name="tracker")


@task(name="fetch_targets")
async def fetch_targets(batch_size: int = 50) -> Any:
    """
    Fetch videos from watchlist needing updates.

    Adaptive Scheduling: Operates on watchlist, not videos table.
    Videos may have been archived but still need tracking.

    Args:
        batch_size: Maximum number of videos to fetch (max 50 for YouTube API)

    Returns:
        List of watchlist records
    """
    watchlist_repo = WatchlistRepository()
    run_logger = get_run_logger()

    try:
        targets = await watchlist_repo.fetch_batch(batch_size)
        run_logger.info(f"Fetched {len(targets)} videos from watchlist (batch_size={batch_size}).")
        return targets
    except Exception as e:
        run_logger.error(f"Failed to fetch tracking targets: {e}")
        return []


@task(name="update_stats")
async def update_stats(videos: List[Dict[str, Any]]) -> int:
    """
    Fetch statistics from YouTube API and store to Vault.

    Adaptive Scheduling:
    - Does NOT update videos table (row may not exist)
    - Stores metrics to Vault as Parquet
    - Updates watchlist schedule based on video age

    Args:
        videos: List of watchlist records

    Returns:
        Number of videos successfully updated
    """
    if not videos:
        return 0

    run_logger = get_run_logger()
    watchlist_repo = WatchlistRepository()

    video_ids = [v["video_id"] for v in videos]
    id_str = ",".join(video_ids)

    run_logger.info(f"Fetching stats for {len(video_ids)} videos...")

    base_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics",
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
        response_json = await execute_with_rate_limit(tracker_executor, make_request)
    except RateLimitError:
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

    now = datetime.now(timezone.utc)
    metrics_data = []
    watchlist_updates = []

    for item in items:
        vid_id = item["id"]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})

        published_at_str = snippet.get("publishedAt")
        if not published_at_str:
            run_logger.warning(f"No publishedAt for {vid_id}, skipping")
            continue

        try:
            if published_at_str.endswith("Z"):
                published_at = datetime.fromisoformat(published_at_str[:-1]).replace(
                    tzinfo=timezone.utc
                )
            else:
                published_at = datetime.fromisoformat(published_at_str)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
        except Exception as e:
            run_logger.warning(f"Failed to parse publishedAt for {vid_id}: {e}")
            continue

        metrics_data.append(
            {
                "video_id": vid_id,
                "timestamp": now.isoformat(),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "published_at": published_at.isoformat(),
            }
        )

        tier, next_track_at = watchlist_repo.calculate_next_track_time(published_at)

        watchlist_updates.append(
            {
                "video_id": vid_id,
                "tracking_tier": tier,
                "last_tracked_at": now,
                "next_track_at": next_track_at,
            }
        )

    if metrics_data:
        try:
            get_vault().append_metrics(metrics_data)
            run_logger.info(f"✓ Stored {len(metrics_data)} metrics to Vault")
        except Exception as e:
            run_logger.error(f"Failed to store metrics to Vault: {e}")

    if watchlist_updates:
        try:
            await watchlist_repo.update_schedule(watchlist_updates)
            run_logger.info(f"✓ Updated {len(watchlist_updates)} watchlist schedules")
        except Exception as e:
            run_logger.error(f"Failed to update watchlist: {e}")
            return 0

    return len(items)


@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50) -> Dict[str, Any]:
    """
    Execute a complete Tracker cycle using Adaptive Scheduling.

    Adaptive Scheduling: Fetches from watchlist, stores to Vault.

    Args:
        batch_size: Number of videos to process (max 50 for YouTube API)

    Returns:
        Dictionary with cycle statistics
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Tracker Cycle (Adaptive Scheduling) ===")

    stats = {
        "videos_fetched": 0,
        "videos_updated": 0,
        "updates_failed": 0,
    }

    try:
        if batch_size > 50:
            run_logger.warning(f"Batch size {batch_size} exceeds YouTube API limit. Capping at 50.")
            batch_size = 50

        targets = await fetch_targets(batch_size=batch_size)
        stats["videos_fetched"] = len(targets)

        if not targets:
            run_logger.info("No videos need tracking updates. Tracker cycle complete (idle).")
            return stats

        updated_count = await update_stats(targets)
        stats["videos_updated"] = updated_count
        stats["updates_failed"] = len(targets) - updated_count

        run_logger.info(
            f"=== Tracker Cycle Complete === "
            f"Fetched: {stats['videos_fetched']}, "
            f"Updated: {stats['videos_updated']}, "
            f"Failed: {stats['updates_failed']}"
        )

    except RateLimitError:
        run_logger.critical("Tracker Cycle terminated by resiliency strategy (429 Rate Limit)")
        raise
    except Exception as e:
        run_logger.exception(f"Tracker cycle failed with unexpected error: {e}")
        raise

    return stats


def main() -> None:
    try:
        asyncio.run(run_tracker_cycle())
    except RateLimitError as e:
        logger.critical(f"Adaptive Tracker terminated: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("Adaptive Tracker stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Adaptive Tracker failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
