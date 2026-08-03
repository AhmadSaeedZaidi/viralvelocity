import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas.adapters import DatabaseAdapter
from atlas.config import settings
from atlas.models.watchlist import WatchlistItem

logger = logging.getLogger("atlas.repositories.watchlist")

_TIER_INTERVAL = {
    "HOURLY": timedelta(hours=1),
    "DAILY": timedelta(days=1),
    "WEEKLY": timedelta(days=7),
}

# More-frequent-first order used by the drop-one-tier logic.
_TIER_ORDER = ("HOURLY", "DAILY", "WEEKLY")


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
            SELECT w.video_id, w.tracking_tier, w.last_tracked_at, w.next_track_at,
                   w.created_at, v.published_at
            FROM watchlist w
            LEFT JOIN videos v ON v.id = w.video_id
            WHERE w.next_track_at <= NOW()
            ORDER BY w.next_track_at ASC
            LIMIT %s
            FOR UPDATE OF w SKIP LOCKED
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

    async def velocity_views_per_hour(self, video_ids: list[str]) -> dict[str, float | None]:
        """Return views/hour for each video from its two most recent stats rows.

        Measures ``(latest_views - prev_views) / hours_between`` so the decay
        tier can boost hot videos. Videos with <2 samples or a non-positive time
        delta get ``None`` (unknown velocity → age floor). Serves the two newest
        rows per id in one windowed query over the PK ``(video_id, timestamp)``.
        """
        if not video_ids:
            return {}

        rows = await self._fetch_all(
            """
            SELECT video_id, views, timestamp
            FROM (
                SELECT video_id, views, timestamp,
                       ROW_NUMBER() OVER (
                           PARTITION BY video_id ORDER BY timestamp DESC
                       ) AS rn
                FROM video_stats_log
                WHERE video_id = ANY(%s)
            ) ranked
            WHERE rn <= 2
            ORDER BY video_id, timestamp DESC
            """,
            (video_ids,),
        )

        pairs: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            pairs.setdefault(r["video_id"], []).append(r)

        result: dict[str, float | None] = {}
        for vid in video_ids:
            sample = pairs.get(vid) or []
            if len(sample) < 2:
                result[vid] = None
                continue
            latest, prev = sample[0], sample[1]
            if latest["views"] is None or prev["views"] is None:
                result[vid] = None
                continue
            delta_h = (latest["timestamp"] - prev["timestamp"]).total_seconds() / 3600.0
            if delta_h <= 0:
                result[vid] = None
                continue
            delta_v = latest["views"] - prev["views"]
            result[vid] = max(0.0, delta_v / delta_h)
        return result

    def _tier_from_age(self, published_at: datetime) -> str:
        now = datetime.now(UTC)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        age = now - published_at
        if age < timedelta(hours=settings.TRACKER_AGE_HOURLY_HOURS):
            return "HOURLY"
        if age < timedelta(days=settings.TRACKER_AGE_DAILY_DAYS):
            return "DAILY"
        return "WEEKLY"

    def _boost_tier(self, age_tier: str, views_per_hour: float | None) -> str:
        """Velocity override on top of the age floor.

        * HOT velocity  → HOURLY (a growing video stays hourly past the age cutoffs)
        * DEAD velocity → one tier less frequent than the age floor (recedes sooner)
        * otherwise / unknown → the age floor unchanged
        """
        if views_per_hour is None:
            return age_tier
        if views_per_hour >= settings.TRACKER_HOT_VIEWS_PER_HOUR:
            return "HOURLY"
        if views_per_hour <= settings.TRACKER_DEAD_VIEWS_PER_HOUR:
            # Drop one tier, flooring at WEEKLY (never slower than weekly).
            idx = _TIER_ORDER.index(age_tier)
            return _TIER_ORDER[min(idx + 1, len(_TIER_ORDER) - 1)]
        return age_tier

    def calculate_next_track_time(
        self,
        published_at: datetime | None,
        views_per_hour: float | None = None,
        tier: str | None = None,
    ) -> tuple[str, datetime]:
        now = datetime.now(UTC)

        # Age floor; if the videos row is gone (janitor hard-delete), fall back
        # to the current tier so tracking continues indefinitely (designed).
        if published_at is not None:
            age_tier = self._tier_from_age(published_at)
        else:
            age_tier = tier or "WEEKLY"

        effective_tier = self._boost_tier(age_tier, views_per_hour)
        interval = _TIER_INTERVAL[effective_tier]
        return (effective_tier, now + interval)
