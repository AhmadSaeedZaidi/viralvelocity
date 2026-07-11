"""Quality / retention queries for the ``videos`` table.

Supports the ingestion-quality monitor and the short-video purge command.
"""

import logging
from typing import Any

logger = logging.getLogger("atlas.repositories.video.quality")


class VideoQualityMixin:
    async def find_short_video_ids(
        self, max_duration: int, limit: int | None = None
    ) -> list[str]:
        """Return IDs of videos shorter than ``max_duration`` seconds.

        Includes videos that already have artifacts (transcripts, frames, audio,
        video) — the purge command removes those too.
        """
        query = (
            "SELECT id FROM videos "
            "WHERE duration IS NOT NULL AND duration < %s "
            "ORDER BY discovered_at ASC"
        )
        params: tuple[Any, ...] = (max_duration,)
        if limit is not None:
            query += " LIMIT %s"
            params = (max_duration, limit)
        rows = await self._fetch_all(query, params)
        return [row["id"] for row in rows]

    async def delete_videos(self, ids: list[str]) -> int:
        """Permanently delete the given video rows (and their artifacts' rows).

        ``transcripts`` and ``video_stats_log`` cascade via FK; ``watchlist`` has
        no FK so it is deleted explicitly. Returns the number of video rows removed.
        """
        if not ids:
            return 0
        await self._execute(
            "DELETE FROM watchlist WHERE video_id = ANY(%s)", (ids,)
        )
        await self._execute(
            "DELETE FROM videos WHERE id = ANY(%s)", (ids,)
        )
        # Row count from the last statement (videos) is what we report.
        return len(ids)

    async def quality_report(self) -> dict[str, Any]:
        """Aggregate ingestion-quality statistics for monitoring."""
        total = await self._fetch_one("SELECT COUNT(*) AS n FROM videos")
        by_status = await self._fetch_all(
            "SELECT status, COUNT(*) AS n FROM videos GROUP BY status ORDER BY n DESC"
        )
        shorts = await self._fetch_one(
            "SELECT COUNT(*) AS n FROM videos WHERE duration IS NOT NULL AND duration < 180"
        )
        total_duration = await self._fetch_one(
            "SELECT COUNT(*) AS n FROM videos WHERE duration IS NOT NULL"
        )
        buckets = await self._fetch_all(
            """
            SELECT
                CASE
                    WHEN duration < 60  THEN 'under_1m'
                    WHEN duration < 180 THEN '1_3m'
                    WHEN duration < 600 THEN '3_10m'
                    WHEN duration < 1200 THEN '10_20m'
                    ELSE 'over_20m'
                END AS bucket,
                COUNT(*) AS n
            FROM videos
            WHERE duration IS NOT NULL
            GROUP BY bucket
            ORDER BY bucket
            """
        )
        coverage = await self._fetch_one(
            """
            SELECT
                COUNT(*) FILTER (WHERE has_transcript) AS transcripts,
                COUNT(*) FILTER (WHERE has_visuals)     AS visuals,
                COUNT(*) FILTER (WHERE has_audio)        AS audio,
                COUNT(*) FILTER (WHERE has_video)        AS video
            FROM videos
            """
        )
        return {
            "total_videos": total["n"] if total else 0,
            "total_with_duration": total_duration["n"] if total_duration else 0,
            "shorts_under_3m": shorts["n"] if shorts else 0,
            "by_status": {row["status"]: row["n"] for row in by_status},
            "duration_buckets": {row["bucket"]: row["n"] for row in buckets},
            "artifact_coverage": dict(coverage) if coverage else {},
        }
