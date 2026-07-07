import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from atlas.adapters import DatabaseAdapter
from atlas.config import settings
from atlas.models.video import Video, VideoStats

logger = logging.getLogger("atlas.repositories.video")

_CHANNEL_TITLE_PENDING = "Pending channel index"

ARCHIVAL_BATCH_SIZE = 100


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

    async def get_by_id(self, video_id: str) -> Video | None:
        row = await self._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))
        return Video.model_validate(row) if row else None

    async def get_latest_stats(self, video_id: str) -> VideoStats | None:
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
        now = datetime.now(UTC)

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

    # ── State Machine: Claim methods (atomic PENDING → PROCESSING) ────────

    async def claim_scribe_batch(self, batch_size: int = 10) -> list[Video]:
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING'
            WHERE id IN (
                SELECT id FROM videos
                WHERE status IN ('PENDING', 'PROCESSING')
                  AND has_transcript = FALSE
                ORDER BY discovered_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (batch_size,),
        )
        return [Video.model_validate(r) for r in rows]

    async def claim_painter_batch(self, batch_size: int = 5) -> list[Video]:
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING'
            WHERE id IN (
                SELECT id FROM videos
                WHERE status IN ('PENDING', 'PROCESSING')
                  AND has_visuals = FALSE
                ORDER BY discovered_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (batch_size,),
        )
        return [Video.model_validate(r) for r in rows]

    # ── State Machine: Transition helpers ─────────────────────────────────

    async def mark_transcript_safe(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET has_transcript = TRUE,
                last_updated_at = %s,
                status = CASE WHEN has_visuals THEN 'PROCESSED' ELSE status END
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_visuals_safe(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET has_visuals = TRUE,
                last_updated_at = %s,
                status = CASE WHEN has_transcript THEN 'PROCESSED' ELSE status END
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_done(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET status = 'PROCESSED', last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_failed(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET status = 'FAILED', last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def mark_archived(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET status = 'ARCHIVED', archived_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    # ── Janitor: Sweep Phase ──────────────────────────────────────────────

    async def sweep_archivable(self, batch_size: int = ARCHIVAL_BATCH_SIZE) -> list[Video]:
        cutoff = datetime.now(UTC) - timedelta(days=settings.JANITOR_RETENTION_DAYS)
        rows = await self._fetch_all(
            """
            SELECT * FROM videos
            WHERE status = 'PROCESSED'
              AND last_updated_at < %s
            ORDER BY last_updated_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (cutoff, batch_size),
        )
        return [Video.model_validate(r) for r in rows]

    async def count_archivable(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=settings.JANITOR_RETENTION_DAYS)
        row = await self._fetch_one(
            """
            SELECT COUNT(*) as total FROM videos
            WHERE status = 'PROCESSED'
              AND last_updated_at < %s
            """,
            (cutoff,),
        )
        return cast(int, row["total"]) if row else 0

    # ── Janitor: Hand-off Phase (serialize + vault + verify + purge) ──────

    async def archive_video_batch(
        self, videos: list[Video], dry_run: bool = False
    ) -> dict[str, Any]:
        """Serialize video metadata + stats to vault, then mark ARCHIVED and purge.

        Vault operations are offloaded to a thread-pool via ``asyncio.to_thread``
        since the vault adapter is synchronous (HF/GCS SDK).

        On vault failure: emits a ``janitor.archive_failed`` event, logs the error,
        and **leaves the hot DB record untouched** (rollback by omission).
        """
        import asyncio

        from atlas.events import events
        from atlas.vault import get_vault

        if not videos:
            return {"archived": 0, "failed": 0}

        if dry_run:
            return {
                "archived": 0,
                "dry_run": True,
                "would_archive": len(videos),
                "video_ids": [v.id for v in videos],
            }

        v = get_vault()
        archived_count = 0
        failed_count = 0
        failed_ids: list[str] = []

        for video in videos:
            try:
                metadata: dict[str, Any] = {
                    "id": video.id,
                    "channel_id": video.channel_id,
                    "title": video.title,
                    "published_at": video.published_at.isoformat() if video.published_at else None,
                    "duration": video.duration,
                    "tags": video.tags,
                    "category_id": video.category_id,
                    "discovered_at": video.discovered_at.isoformat()
                    if video.discovered_at
                    else None,
                    "last_updated_at": video.last_updated_at.isoformat()
                    if video.last_updated_at
                    else None,
                    "has_transcript": video.has_transcript,
                    "has_visuals": video.has_visuals,
                }

                latest_stats = await self.get_latest_stats(video.id)
                if latest_stats:
                    metadata["stats"] = {
                        "views": latest_stats.views,
                        "likes": latest_stats.likes,
                        "comment_count": latest_stats.comment_count,
                        "last_tracked_at": latest_stats.timestamp.isoformat()
                        if latest_stats.timestamp
                        else None,
                    }

                date_key = datetime.now(UTC).strftime("%Y-%m-%d")

                # Hand-off: write metadata to vault (synchronous → thread)
                await asyncio.to_thread(v.store_metadata, video.id, metadata, date_key)

                # Verification interlock
                if settings.JANITOR_SAFETY_CHECK:
                    verified = await asyncio.to_thread(v.fetch_metadata, video.id, date_key)
                    if not verified:
                        raise RuntimeError(
                            f"Vault verification failed for {video.id} — "
                            "metadata not found after write"
                        )

                # Mark as ARCHIVED
                await self.mark_archived(video.id)

                # Purge stats + transcript rows from hot tier
                await self._execute("DELETE FROM video_stats_log WHERE video_id = %s", (video.id,))
                await self._execute("DELETE FROM transcripts WHERE video_id = %s", (video.id,))

                archived_count += 1

            except Exception as exc:
                failed_count += 1
                failed_ids.append(video.id)
                logger.error(f"Archival failed for {video.id}: {exc}")
                await events.emit(
                    "janitor.archive_failed",
                    video.id,
                    {"error": str(exc), "retention_days": settings.JANITOR_RETENTION_DAYS},
                )

        return {
            "archived": archived_count,
            "failed": failed_count,
            "failed_ids": failed_ids,
        }

    # ── Legacy legacy (stats archival, old janitor) ───────────────────────

    async def fetch_scribe_batch(self, batch_size: int = 10) -> list[Video]:
        return await self.claim_scribe_batch(batch_size)

    async def fetch_painter_batch(self, batch_size: int = 5) -> list[Video]:
        return await self.claim_painter_batch(batch_size)

    async def ingest_video_metadata(
        self, video_data: dict[str, Any], priority_override: int | None = None
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
                    (channel_id, channel_title, datetime.now(UTC), pend, pend),
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
                    datetime.now(UTC),
                ),
            )
            await conn.commit()

    async def archive_cold_stats(self, retention_days: int = 7, batch_size: int = 5000) -> int:
        from atlas.vault import get_vault

        cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)

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
            stats = [dict(zip(columns, row, strict=True)) for row in rows]

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

        cutoff_date = datetime.now(UTC) - timedelta(days=settings.JANITOR_RETENTION_DAYS)
        safety_clause = ""
        if settings.JANITOR_SAFETY_CHECK:
            safety_clause = "AND (has_transcript = TRUE OR has_visuals = TRUE)"

        count_result = await self._fetch_one(
            f"""
            SELECT COUNT(*) as total
            FROM videos
            WHERE discovered_at < %s
              AND status IN ('PROCESSED', 'DONE')
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
              AND status IN ('PROCESSED', 'DONE')
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
