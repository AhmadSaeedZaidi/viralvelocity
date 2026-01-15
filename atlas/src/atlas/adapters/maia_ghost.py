"""Ghost Tracking extensions for MaiaDAO."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger("atlas.adapters.maia.ghost")


class GhostTrackingMixin:
    """Persistent video tracking beyond Janitor cleanup."""

    async def add_to_watchlist(self, video_id: str, tier: str = "HOURLY") -> None:
        query = """
            INSERT INTO watchlist (video_id, tracking_tier, next_track_at, created_at)
            VALUES (%s, %s, NOW(), NOW())
            ON CONFLICT (video_id) DO NOTHING
        """
        await self._execute(query, (video_id, tier))
        logger.debug(f"Added {video_id} to watchlist with tier {tier}")

    async def fetch_tracking_batch(self, batch_size: int = 50) -> List[Dict[str, Any]]:
        query = """
            SELECT video_id, tracking_tier, last_tracked_at, next_track_at
            FROM watchlist
            WHERE next_track_at <= NOW()
            ORDER BY next_track_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        return await self._fetch_all(query, (batch_size,))

    async def update_watchlist_schedule(self, updates: List[Dict[str, Any]]) -> None:
        """
        Batch update watchlist tracking schedule.

        Args:
            updates: List of dicts with keys:
                - video_id: str
                - tracking_tier: str (HOURLY, DAILY, WEEKLY)
                - last_tracked_at: datetime
                - next_track_at: datetime
        """
        if not updates:
            return

        query = """
            UPDATE watchlist
            SET tracking_tier = %s,
                last_tracked_at = %s,
                next_track_at = %s
            WHERE video_id = %s
        """

        params_list = [
            (
                update["tracking_tier"],
                update["last_tracked_at"],
                update["next_track_at"],
                update["video_id"],
            )
            for update in updates
        ]

        await self._execute_many(query, params_list)
        logger.info(f"Updated {len(updates)} watchlist schedules")

    def calculate_next_track_time(
        self, published_at: datetime, tier: str = None
    ) -> tuple[str, datetime]:
        now = datetime.now(timezone.utc)

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        age = now - published_at

        if age < timedelta(hours=24):
            tier = "HOURLY"
            next_track_at = now + timedelta(hours=1)
        elif age < timedelta(days=7):
            tier = "DAILY"
            next_track_at = now + timedelta(days=1)
        else:
            tier = "WEEKLY"
            next_track_at = now + timedelta(days=7)

        return (tier, next_track_at)
