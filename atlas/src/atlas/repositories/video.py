import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from atlas.adapters import DatabaseAdapter
from atlas.config import settings
from atlas.models.video import Video, VideoStats

logger = logging.getLogger("atlas.repositories.video")

_CHANNEL_TITLE_PENDING = "Pending channel index"


class VideoRepository(DatabaseAdapter):
    async def save(self, video: Video) -> None:
        query = """
            INSERT INTO videos (
                id, channel_id, title, published_at, duration,
                tags, category_id, default_language, wiki_topics,
                discovered_at, last_updated_at, status,
                has_transcript, has_visuals
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                channel_id = COALESCE(EXCLUDED.channel_id, videos.channel_id),
                title = EXCLUDED.title,
                published_at = COALESCE(EXCLUDED.published_at, videos.published_at),
                tags = COALESCE(EXCLUDED.tags, videos.tags),
                category_id = COALESCE(EXCLUDED.category_id, videos.category_id),
                status = COALESCE(EXCLUDED.status, videos.status)
        """
        await self._execute(
            query,
            (
                video.id,
                video.channel_id,
                video.title,
                video.published_at,
                video.duration,
                video.tags,
                video.category_id,
                video.default_language,
                video.wiki_topics,
                video.discovered_at,
                video.last_updated_at,
                video.status,
                video.has_transcript,
                video.has_visuals,
            ),
        )

    async def get_by_id(self, video_id: str) -> Optional[Video]:
        row = await self._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))
        return Video.model_validate(row) if row else None

    async def get_latest_stats(self, video_id: str) -> Optional[VideoStats]:
        row = await self._fetch_one(
            """
            SELECT video_id, views, likes, comment_count, timestamp
            FROM video_stats_log
            WHERE video_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (video_id,),
        )
        return VideoStats.model_validate(row) if row else None

    async def fetch_tracker_targets(self, batch_size: int = 50) -> list[Video]:
        now = datetime.now(timezone.utc)

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
        """
        params_list = [
            (s.video_id, s.views, s.likes, s.comment_count, s.timestamp) for s in stats_list
        ]
        await self._execute_many(query, params_list)
        logger.info(f"Logged {len(stats_list)} stats to hot tier")

    async def update_stats_batch(self, updates: list[dict[str, Any]]) -> None:
        timestamp_statement = "UPDATE videos SET last_updated_at = %s WHERE id = %s"
        log_statement = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """
        now = datetime.now(timezone.utc)

        async with self._cursor() as cur:
            for update in updates:
                vid = update["id"]
                stats = update.get("statistics", {})
                await cur.execute(timestamp_statement, (now, vid))
                await cur.execute(
                    log_statement,
                    (
                        vid,
                        stats.get("viewCount"),
                        stats.get("likeCount"),
                        stats.get("commentCount"),
                        now,
                    ),
                )

    async def fetch_scribe_batch(self, batch_size: int = 10) -> list[Video]:
        rows = await self._fetch_all(
            """
            SELECT * FROM videos
            WHERE status = 'PENDING'
              AND has_transcript = FALSE
            ORDER BY discovered_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (batch_size,),
        )
        return [Video.model_validate(r) for r in rows]

    async def fetch_painter_batch(self, batch_size: int = 5) -> list[Video]:
        rows = await self._fetch_all(
            """
            SELECT * FROM videos
            WHERE status = 'PENDING'
              AND has_visuals = FALSE
            ORDER BY discovered_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (batch_size,),
        )
        return [Video.model_validate(r) for r in rows]

    async def mark_transcript_safe(self, video_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self._execute(
            """
            UPDATE videos
            SET has_transcript = TRUE, last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_visuals_safe(self, video_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self._execute(
            """
            UPDATE videos
            SET has_visuals = TRUE, last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_done(self, video_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self._execute(
            """
            UPDATE videos
            SET status = 'DONE', last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_failed(self, video_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self._execute(
            """
            UPDATE videos
            SET status = 'FAILED', last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def ingest_video_metadata(
        self, video_data: dict[str, Any], priority_override: Optional[int] = None
    ) -> None:
        snippet = video_data.get("snippet", {})
        channel_id = snippet.get("channelId")
        channel_title = (
            snippet.get("channelTitle")
            or snippet.get("channel")
            or (_CHANNEL_TITLE_PENDING if channel_id else None)
        )

        async with self._connection() as conn:
            if channel_id and channel_title:
                channel_upsert = """
                    INSERT INTO channels (id, title, created_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = CASE
                            WHEN BTRIM(EXCLUDED.title) <> ''
                                AND EXCLUDED.title <> %s
                            THEN EXCLUDED.title
                            WHEN BTRIM(channels.title) <> ''
                                AND channels.title <> %s
                            THEN channels.title
                            ELSE EXCLUDED.title
                        END
                """
                pend = _CHANNEL_TITLE_PENDING
                await conn.execute(
                    channel_upsert,
                    (channel_id, channel_title, datetime.now(timezone.utc), pend, pend),
                )

            video_query = """
                INSERT INTO videos (
                    id, channel_id, title, published_at,
                    tags, category_id, default_language, discovered_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """
            vid_id = video_data.get("id")
            if isinstance(vid_id, dict):
                vid_id = vid_id.get("videoId")

            if not vid_id:
                await conn.commit()
                return

            await conn.execute(
                video_query,
                (
                    vid_id,
                    channel_id,
                    snippet.get("title"),
                    snippet.get("publishedAt"),
                    snippet.get("tags", []),
                    snippet.get("categoryId"),
                    snippet.get("defaultLanguage"),
                    datetime.now(timezone.utc),
                ),
            )
            await conn.commit()

    async def archive_cold_stats(self, retention_days: int = 7, batch_size: int = 5000) -> int:
        from atlas.vault import get_vault

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

        async with self._connection() as conn:
            cur = await conn.execute(
                """
                SELECT video_id, views, likes, comment_count, timestamp
                FROM video_stats_log
                WHERE timestamp < %s
                ORDER BY timestamp ASC
                LIMIT %s
                FOR UPDATE
                """,
                (cutoff_date, batch_size),
            )
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchall()
            stats = [dict(zip(columns, row)) for row in rows]

            if not stats:
                return 0

            stats_by_date: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for stat in stats:
                ts = stat["timestamp"]
                date_str = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else ts[:10]
                iso_ts = ts.isoformat() if isinstance(ts, datetime) else ts
                stats_by_date[date_str].append(
                    {
                        "video_id": stat["video_id"],
                        "views": stat["views"],
                        "likes": stat["likes"],
                        "comment_count": stat["comment_count"],
                        "timestamp": iso_ts,
                    }
                )

            v = get_vault()
            for date_str, day_stats in stats_by_date.items():
                v.append_metrics(day_stats, date=date_str)

            video_ids = [s["video_id"] for s in stats]
            timestamps = [s["timestamp"] for s in stats]
            await conn.execute(
                """
                DELETE FROM video_stats_log
                WHERE (video_id, timestamp) IN (
                    SELECT unnest(%s::text[]), unnest(%s::timestamp[])
                )
                """,
                (video_ids, timestamps),
            )

            logger.info(f"Archived and purged {len(stats)} stats from hot tier")
            return len(stats)

    async def run_janitor(self, dry_run: bool = False) -> dict[str, Any]:
        if not settings.JANITOR_ENABLED:
            return {"deleted": 0, "reason": "disabled"}

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=settings.JANITOR_RETENTION_DAYS)
        safety_clause = ""
        if settings.JANITOR_SAFETY_CHECK:
            safety_clause = "AND (has_transcript = TRUE OR has_visuals = TRUE)"

        count_result = await self._fetch_one(
            f"""
            SELECT COUNT(*) as total
            FROM videos
            WHERE discovered_at < %s
              AND status = 'DONE'
              {safety_clause}
            """,
            (cutoff_date,),
        )
        total_to_delete = count_result["total"] if count_result else 0

        if total_to_delete == 0:
            return {"deleted": 0, "reason": "none_eligible"}

        if dry_run:
            return {"deleted": 0, "dry_run": True, "would_delete": total_to_delete}

        await self._execute(
            f"""
            DELETE FROM videos
            WHERE discovered_at < %s
              AND status = 'DONE'
              {safety_clause}
            """,
            (cutoff_date,),
        )
        return {
            "deleted": total_to_delete,
            "cutoff_date": cutoff_date.isoformat(),
            "retention_days": settings.JANITOR_RETENTION_DAYS,
            "safety_check_enabled": settings.JANITOR_SAFETY_CHECK,
        }
