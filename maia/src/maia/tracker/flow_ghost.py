"""
Maia Tracker - Ghost Tracking Implementation

Monitors viral velocity using the Ghost Tracking watchlist.
Operates independently of the videos table - tracks videos forever
even after they've been cleaned up from the hot queue.

Key Changes:
- Uses watchlist table instead of videos table
- Stores metrics in Vault (Parquet) instead of SQL
- Adaptive tracking tiers based on video age
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from prefect import flow, get_run_logger, task

from atlas.adapters.maia import MaiaDAO
from atlas.utils import HydraExecutor, KeyRing
from atlas.vault import vault

logger = logging.getLogger(__name__)

tracker_keys = KeyRing("tracking")
tracker_executor = HydraExecutor(tracker_keys, agent_name="tracker")


@task(name="fetch_targets")
async def fetch_targets(batch_size: int = 50) -> List[Dict[str, Any]]:
    """
    Fetch videos from watchlist needing updates.

    Ghost Tracking: Operates on watchlist, not videos table.
    Videos may have been cleaned up but still need tracking.

    Args:
        batch_size: Maximum number of videos to fetch (max 50 for YouTube API)

    Returns:
        List of watchlist records
    """
    dao = MaiaDAO()
    run_logger = get_run_logger()

    try:
        targets = await dao.fetch_tracking_batch(batch_size)
        run_logger.info(f"Fetched {len(targets)} videos from watchlist (batch_size={batch_size}).")
        return targets
    except Exception as e:
        run_logger.error(f"Failed to fetch tracking targets: {e}")
        return []


@task(name="update_stats")
async def update_stats(videos: List[Dict[str, Any]]) -> int:
    """
    Fetch statistics from YouTube API and store to Vault.

    Ghost Tracking Changes:
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
    dao = MaiaDAO()

    video_ids = [v["video_id"] for v in videos]
    id_str = ",".join(video_ids)

    run_logger.info(f"Fetching stats for {len(video_ids)} videos...")

    base_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics",  # Need snippet for publishedAt
        "id": id_str,
    }

    # Define request function for HydraExecutor
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

    # Execute with HydraExecutor
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

    # Process results and prepare for Vault and watchlist updates
    now = datetime.now(timezone.utc)
    metrics_data = []
    watchlist_updates = []

    for item in items:
        vid_id = item["id"]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})

        # Extract published_at for tier calculation
        published_at_str = snippet.get("publishedAt")
        if not published_at_str:
            run_logger.warning(f"No publishedAt for {vid_id}, skipping")
            continue

        # Parse published_at
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

        # Prepare metrics for Vault
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

        # Calculate next tracking tier and time
        tier, next_track_at = dao.calculate_next_track_time(published_at)

        watchlist_updates.append(
            {
                "video_id": vid_id,
                "tracking_tier": tier,
                "last_tracked_at": now,
                "next_track_at": next_track_at,
            }
        )

    # Store metrics to Vault (Parquet)
    if metrics_data:
        try:
            vault.append_metrics(metrics_data)
            run_logger.info(f"✓ Stored {len(metrics_data)} metrics to Vault")
        except Exception as e:
            run_logger.error(f"Failed to store metrics to Vault: {e}")
            # Continue anyway to update watchlist

    # Update watchlist schedules
    if watchlist_updates:
        try:
            await dao.update_watchlist_schedule(watchlist_updates)
            run_logger.info(f"✓ Updated {len(watchlist_updates)} watchlist schedules")
        except Exception as e:
            run_logger.error(f"Failed to update watchlist: {e}")
            return 0

    return len(items)


@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50) -> Dict[str, Any]:
    """
    Execute a complete Tracker cycle using Ghost Tracking.

    Ghost Tracking: Fetches from watchlist, stores to Vault.

    Args:
        batch_size: Number of videos to process (max 50 for YouTube API)

    Returns:
        Dictionary with cycle statistics
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Tracker Cycle (Ghost Tracking) ===")

    stats = {
        "videos_fetched": 0,
        "videos_updated": 0,
        "updates_failed": 0,
    }

    try:
        # Enforce YouTube API batch limit
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

    except SystemExit:
        # Hydra Protocol: Rate limit detected - propagate immediately
        run_logger.critical("Tracker Cycle terminated by Hydra Protocol (429 Rate Limit)")
        raise
    except Exception as e:
        run_logger.exception(f"Tracker cycle failed with unexpected error: {e}")
        raise

    return stats


def main() -> None:
    """Entry point for running the Ghost Tracker as a standalone service."""
    try:
        asyncio.run(run_tracker_cycle())  # type: ignore[arg-type]
    except SystemExit as e:
        # Hydra Protocol: Exit with specific code for rate limit
        logger.critical(f"Ghost Tracker terminated: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("Ghost Tracker stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Ghost Tracker failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
