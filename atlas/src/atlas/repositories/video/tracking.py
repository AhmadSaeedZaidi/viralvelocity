import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas.adapters import DatabaseAdapter
from atlas.models.video import Video, VideoStats

logger = logging.getLogger("atlas.repositories.video.tracking")


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class VideoTrackingMixin(DatabaseAdapter):
    async def fetch_tracker_targets(self, batch_size: int = 50) -> list[Video]:
        now = datetime.now(UTC)
        cooldown = now - timedelta(hours=1)

        # Track the whole eligible corpus, not just today's discoveries: order by
        # staleness (never-tracked first, then oldest-tracked) so the long tail of
        # previously-discovered videos is revisited instead of drifting stale
        # forever. The 1h cooldown makes this a natural rotating queue — after a
        # video is tracked it falls to the back and comes around again ~once per
        # corpus-size/batch cycles, spreading YouTube quota evenly.
        query = """
            SELECT * FROM videos
            WHERE status NOT IN ('ARCHIVED', 'FAILED')
              AND (last_tracked_at IS NULL OR last_tracked_at < %s)
            ORDER BY last_tracked_at ASC NULLS FIRST
            LIMIT %s
        """
        rows = await self._fetch_all(query, (cooldown, batch_size))
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

        timestamp_statement = "UPDATE videos SET last_tracked_at = %s WHERE id = %s"
        log_statement = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (video_id, timestamp) DO UPDATE SET
                views = EXCLUDED.views,
                likes = EXCLUDED.likes,
                comment_count = EXCLUDED.comment_count
        """
        now = datetime.now(UTC)

        ts_params = [(now, u["id"]) for u in updates]
        log_params = [
            (
                u["id"],
                _to_int(u.get("statistics", {}).get("viewCount")),
                _to_int(u.get("statistics", {}).get("likeCount")),
                _to_int(u.get("statistics", {}).get("commentCount")),
                now,
            )
            for u in updates
        ]

        async with self._cursor() as cur:
            await cur.executemany(timestamp_statement, ts_params)
            await cur.executemany(log_statement, log_params)
            await cur.connection.commit()

    async def mark_tracked(self, video_ids: list[str]) -> int:
        """Advance ``last_tracked_at`` for videos without logging stats.

        Called when the YouTube API returns no item for an id (deleted,
        private, or geo-blocked). Bumps ``last_tracked_at`` so a permanently
        unavailable video rotates out of the tracker queue front instead of
        being re-queried on every 60s cycle forever (mirrors the streamer's
        retry cooldown). Returns the number of videos marked.
        """
        if not video_ids:
            return 0

        now = datetime.now(UTC)
        rows = await self._fetch_all(
            """
            UPDATE videos
            SET last_tracked_at = %s
            WHERE id = ANY(%s)
            RETURNING id
            """,
            (now, video_ids),
        )
        return len(rows)

    async def pipeline_snapshot(self) -> dict[str, Any]:
        """Return a point-in-time snapshot of pipeline health for reporting.

        Includes lifecycle status counts, total videos, transcript / visual
        coverage, tracker metrics, and pipeline velocity.
        """
        now = datetime.now(UTC)

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

        tracked_ever = await self._fetch_scalar(
            "SELECT COUNT(*) FROM videos WHERE last_tracked_at IS NOT NULL"
        ) or 0
        tracked_1h = await self._fetch_scalar(
            "SELECT COUNT(*) FROM videos WHERE last_tracked_at > %s", (now - timedelta(hours=1),)
        ) or 0
        tracked_24h = await self._fetch_scalar(
            "SELECT COUNT(*) FROM videos WHERE last_tracked_at > %s", (now - timedelta(hours=24),)
        ) or 0

        stats_log_size = await self._fetch_scalar("SELECT COUNT(*) FROM video_stats_log") or 0

        pipeline_phase_rows = await self._fetch_all(
            "SELECT COALESCE(pipeline_phase, 'NONE') AS phase, COUNT(*) AS c FROM videos GROUP BY pipeline_phase"
        )
        phase_counts = {r["phase"]: r["c"] for r in pipeline_phase_rows}

        return {
            "total": total,
            "status_counts": status_counts,
            "transcripts": transcripts,
            "with_visuals": with_visuals,
            "audios": audios,
            "ingested_1h": ingested_1h,
            "tracked_ever": tracked_ever,
            "tracked_1h": tracked_1h,
            "tracked_24h": tracked_24h,
            "stats_log_size": stats_log_size,
            "phase_counts": phase_counts,
        }
