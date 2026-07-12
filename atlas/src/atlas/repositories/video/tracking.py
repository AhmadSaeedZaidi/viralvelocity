import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas.adapters import DatabaseAdapter
from atlas.models.video import Video, VideoStats

logger = logging.getLogger("atlas.repositories.video.tracking")


class VideoTrackingMixin(DatabaseAdapter):
    async def fetch_tracker_targets(self, batch_size: int = 50) -> list[Video]:
        now = datetime.now(UTC)

        z1_cutoff = now - timedelta(hours=24)
        z1_thresh = now - timedelta(hours=1)
        z2_cutoff = now - timedelta(days=7)
        z2_thresh = now - timedelta(hours=6)
        z3_thresh = now - timedelta(hours=24)

        query = """
            WITH candidates AS (
                SELECT *, 1 as zone FROM videos
                WHERE published_at >= %s
                  AND (last_updated_at IS NULL OR last_updated_at < %s)
                UNION ALL
                SELECT *, 2 as zone FROM videos
                WHERE published_at < %s AND published_at >= %s
                  AND (last_updated_at IS NULL OR last_updated_at < %s)
                UNION ALL
                SELECT *, 3 as zone FROM videos
                WHERE published_at < %s
                  AND (last_updated_at IS NULL OR last_updated_at < %s)
            )
            SELECT * FROM candidates
            ORDER BY zone ASC, last_updated_at ASC NULLS FIRST
            LIMIT %s
        """
        rows = await self._fetch_all(
            query,
            (
                z1_cutoff,
                z1_thresh,
                z1_cutoff,
                z2_cutoff,
                z2_thresh,
                z2_cutoff,
                z3_thresh,
                batch_size,
            ),
        )
        return [Video.model_validate(r) for r in rows]

    async def log_stats_batch(self, stats_list: list[VideoStats]) -> None:
        if not stats_list:
            return

        query = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (video_id, timestamp) DO UPDATE SET
                views = EXCLUDED.views,
                likes = EXCLUDED.likes,
                comment_count = EXCLUDED.comment_count
        """
        params_list = [
            (s.video_id, s.views, s.likes, s.comment_count, s.timestamp) for s in stats_list
        ]
        await self._execute_many(query, params_list)
        logger.info(f"Logged {len(stats_list)} stats to hot tier")

    async def update_stats_batch(self, updates: list[dict[str, Any]]) -> None:
        if not updates:
            return

        timestamp_statement = "UPDATE videos SET last_updated_at = %s WHERE id = %s"
        log_statement = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (video_id, timestamp) DO UPDATE SET
                views = EXCLUDED.views,
                likes = EXCLUDED.likes,
                comment_count = EXCLUDED.comment_count
        """
        now = datetime.now(UTC)

        # Batch both statements into two round-trips (not 2*N) so the server
        # reuses the prepared plan.
        ts_params = [(now, u["id"]) for u in updates]
        log_params = [
            (
                u["id"],
                u.get("statistics", {}).get("viewCount"),
                u.get("statistics", {}).get("likeCount"),
                u.get("statistics", {}).get("commentCount"),
                now,
            )
            for u in updates
        ]

        async with self._cursor() as cur:
            await cur.executemany(timestamp_statement, ts_params)
            await cur.executemany(log_statement, log_params)
            await cur.connection.commit()

    async def pipeline_snapshot(self) -> dict[str, Any]:
        """Return a point-in-time snapshot of pipeline health for reporting.

        Includes lifecycle status counts, total videos, transcript / visual
        coverage, and the number of videos discovered in the last hour.
        """
        status_rows = await self._fetch_all(
            "SELECT status, COUNT(*) AS c FROM videos GROUP BY status"
        )
        status_counts = {r["status"]: r["c"] for r in status_rows}

        total = await self._fetch_scalar("SELECT COUNT(*) FROM videos") or 0
        transcripts = await self._fetch_scalar("SELECT COUNT(*) FROM transcripts") or 0
        with_visuals = (
            await self._fetch_scalar("SELECT COUNT(*) FROM videos WHERE has_visuals") or 0
        )
        audios = await self._fetch_scalar("SELECT COUNT(*) FROM videos WHERE has_audio") or 0
        ingested_1h = (
            await self._fetch_scalar(
                "SELECT COUNT(*) FROM videos WHERE discovered_at > NOW() - INTERVAL '1 hour'"
            )
            or 0
        )

        return {
            "total": total,
            "status_counts": status_counts,
            "transcripts": transcripts,
            "with_visuals": with_visuals,
            "audios": audios,
            "ingested_1h": ingested_1h,
        }
