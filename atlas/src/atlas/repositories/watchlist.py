import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas.adapters import DatabaseAdapter
from atlas.models.watchlist import WatchlistItem

logger = logging.getLogger("atlas.repositories.watchlist")


class WatchlistRepository(DatabaseAdapter):
    async def add(self, video_id: str, tier: str = "HOURLY") -> None:
        await self._execute(
            """
            INSERT INTO watchlist (video_id, tracking_tier, next_track_at, created_at)
            VALUES (%s, %s, NOW(), NOW())
            ON CONFLICT (video_id) DO NOTHING
            """,
            (video_id, tier),
        )

    async def fetch_batch(self, batch_size: int = 50) -> list[WatchlistItem]:
        rows = await self._fetch_all(
            """
            SELECT video_id, tracking_tier, last_tracked_at, next_track_at, created_at
            FROM watchlist
            WHERE next_track_at <= NOW()
            ORDER BY next_track_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (batch_size,),
        )
        return [WatchlistItem.model_validate(r) for r in rows]

    async def update_schedule(self, updates: list[dict[str, Any]]) -> None:
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
            (u["tracking_tier"], u["last_tracked_at"], u["next_track_at"], u["video_id"])
            for u in updates
        ]
        await self._execute_many(query, params_list)

    def calculate_next_track_time(
        self, published_at: datetime, tier: str | None = None
    ) -> tuple[str, datetime]:
        now = datetime.now(UTC)

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)

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
