import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from atlas.adapters import DatabaseAdapter
from atlas.adapters.maia_adaptive_scheduling import AdaptiveSchedulingMixin
from atlas.config import settings

logger = logging.getLogger("atlas.adapters.maia")


class MaiaDAO(DatabaseAdapter, AdaptiveSchedulingMixin):
    """Data Access Object for Maia service."""

    async def fetch_hunter_batch(self, batch_size: int = 10) -> List[Dict[str, Any]]:
        query = """
            SELECT id, query_term, next_page_token, last_searched_at, priority
            FROM search_queue
            WHERE status = 'active'
            ORDER BY priority DESC, mention_count DESC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        return await self._fetch_all(query, (batch_size,))

    async def update_search_state(
        self,
        topic_id: int,
        next_token: Optional[str],
        result_count: int,
        status: str = "active",
    ) -> None:
        query = """
            UPDATE search_queue
            SET next_page_token = %s,
                last_searched_at = %s,
                result_count_total = COALESCE(result_count_total, 0) + %s,
                status = %s
            WHERE id = %s
        """
        now = datetime.now(timezone.utc)
        await self._execute(query, (next_token, now, result_count, status, topic_id))

    async def add_to_search_queue(self, terms: List[str]) -> int:
        if not terms:
            return 0

        unique_terms = list(set(terms))

        query = """
            INSERT INTO search_queue (query_term, priority, mention_count)
            VALUES (%s, 0, 1)
            ON CONFLICT (query_term) 
            DO UPDATE SET mention_count = search_queue.mention_count + 1
        """

        params_list = [(term,) for term in unique_terms]
        await self._execute_many(query, params_list)
        return len(unique_terms)

    _CHANNEL_TITLE_PENDING = "Pending channel index"

    async def ingest_video_metadata(
        self, video_data: Dict[str, Any], priority_override: Optional[int] = None
    ) -> None:
        """Insert video + ensure ``channels`` row exists when ``channelId`` is known.

        YouTube search and some tooling only guarantee ``channelId``; ``channelTitle``
        may be missing. We still insert a channel stub so the ``videos.channel_id`` FK
        is satisfied and :meth:`channel_needs_refresh` is true (no stats), which lets
        :func:`enrich_channels_task` pull full channel metadata via ``channels.list``.
        """
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

        snippet = video_data.get("snippet", {})
        channel_id = snippet.get("channelId")
        channel_title = (
            snippet.get("channelTitle")
            or snippet.get("channel")
            or (self._CHANNEL_TITLE_PENDING if channel_id else None)
        )

        if channel_id and channel_title:
            pend = self._CHANNEL_TITLE_PENDING
            await self._execute(
                channel_upsert,
                (channel_id, channel_title, datetime.now(timezone.utc), pend, pend),
            )

        video_query = """
            INSERT INTO videos (
                id, channel_id, title, published_at, 
                tags, category_id, default_language, 
                discovered_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """

        vid_id = video_data.get("id")
        if isinstance(vid_id, dict):
            vid_id = vid_id.get("videoId")

        if not vid_id:
            return

        published_at = snippet.get("publishedAt")
        title = snippet.get("title")
        tags = snippet.get("tags", [])
        category_id = snippet.get("categoryId")
        default_language = snippet.get("defaultLanguage")

        await self._execute(
            video_query,
            (
                vid_id,
                channel_id,
                title,
                published_at,
                tags,
                category_id,
                default_language,
                datetime.now(timezone.utc),
            ),
        )

    async def ingest_channel_snapshot(self, channel_data: Dict[str, Any]) -> None:
        """Upsert a channel record with full metadata + log a stats snapshot.

        Accepts a YouTube ``channels.list`` API ``item`` shape::

            {
              "id": "UC...",
              "snippet": {"title", "country", "customUrl", "publishedAt"},
              "statistics": {"subscriberCount", "viewCount", "videoCount", ...}
            }

        Behaviour:

        * UPSERTs into ``channels`` with title, country, custom_url, created_at;
          updates ``last_scraped_at`` to ``NOW()`` on every call.
        * INSERTs a row into ``channel_stats_log`` with subscriber/view/video counts
          (no-op only if all stats are missing).

        Idempotent: safe to call repeatedly; produces a fresh stats row each time.
        """
        ch_id = channel_data.get("id")
        if not ch_id:
            logger.warning("ingest_channel_snapshot called without channel id")
            return

        snippet = channel_data.get("snippet", {}) or {}
        stats = channel_data.get("statistics", {}) or {}

        title = snippet.get("title") or ch_id
        country = snippet.get("country")
        custom_url = snippet.get("customUrl")
        created_at = snippet.get("publishedAt")

        upsert_query = """
            INSERT INTO channels (id, title, country, custom_url, created_at, last_scraped_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                country = COALESCE(EXCLUDED.country, channels.country),
                custom_url = COALESCE(EXCLUDED.custom_url, channels.custom_url),
                created_at = COALESCE(channels.created_at, EXCLUDED.created_at),
                last_scraped_at = EXCLUDED.last_scraped_at
        """
        now = datetime.now(timezone.utc)
        await self._execute(
            upsert_query,
            (ch_id, title, country, custom_url, created_at, now),
        )

        def _to_int(v: Any) -> Optional[int]:
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        sub_count = _to_int(stats.get("subscriberCount"))
        view_count = _to_int(stats.get("viewCount"))
        video_count = _to_int(stats.get("videoCount"))

        if sub_count is None and view_count is None and video_count is None:
            logger.info(f"Channel {ch_id} upserted; no statistics provided to log")
            return

        log_query = """
            INSERT INTO channel_stats_log (channel_id, timestamp, view_count, subscriber_count, video_count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (channel_id, timestamp) DO NOTHING
        """
        await self._execute(log_query, (ch_id, now, view_count, sub_count, video_count))
        logger.info(
            f"Channel snapshot {ch_id}: subs={sub_count}, views={view_count}, videos={video_count}"
        )

    async def channel_needs_refresh(self, channel_id: str, max_age_hours: int = 24) -> bool:
        """Return True if the channel has no stats row newer than ``max_age_hours``."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        row = await self._fetch_one(
            """
            SELECT 1 FROM channel_stats_log
            WHERE channel_id = %s AND timestamp >= %s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (channel_id, cutoff),
        )
        return row is None

    async def get_video_by_id(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single ``videos`` row by primary key."""
        return await self._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))

    async def get_channel_by_id(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single ``channels`` row by primary key."""
        return await self._fetch_one("SELECT * FROM channels WHERE id = %s", (channel_id,))

    async def get_latest_video_stats(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the most recent ``video_stats_log`` row for a given video."""
        return await self._fetch_one(
            """
            SELECT video_id, views, likes, comment_count, timestamp
            FROM video_stats_log
            WHERE video_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (video_id,),
        )

    async def get_latest_channel_stats(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the most recent ``channel_stats_log`` row for a given channel."""
        return await self._fetch_one(
            """
            SELECT channel_id, subscriber_count, view_count, video_count, timestamp
            FROM channel_stats_log
            WHERE channel_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (channel_id,),
        )

    async def fetch_tracker_targets(self, batch_size: int = 50) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)

        z1_cutoff = now - timedelta(hours=24)
        z1_thresh = now - timedelta(hours=1)

        z2_cutoff = now - timedelta(days=7)
        z2_thresh = now - timedelta(hours=6)

        z3_thresh = now - timedelta(hours=24)

        query = """
            WITH candidates AS (
                SELECT id, title, published_at, last_updated_at, 1 as priority
                FROM videos
                WHERE published_at >= %s
                  AND (last_updated_at IS NULL OR last_updated_at < %s)
                UNION ALL
                SELECT id, title, published_at, last_updated_at, 2 as priority
                FROM videos
                WHERE published_at < %s AND published_at >= %s
                  AND (last_updated_at IS NULL OR last_updated_at < %s)
                UNION ALL
                SELECT id, title, published_at, last_updated_at, 3 as priority
                FROM videos
                WHERE published_at < %s
                  AND (last_updated_at IS NULL OR last_updated_at < %s)
            )
            SELECT * FROM candidates
            ORDER BY priority ASC, last_updated_at ASC NULLS FIRST
            LIMIT %s
        """

        return await self._fetch_all(
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

    async def log_video_stats_batch(self, stats_list: List[Dict[str, Any]]) -> None:
        """
        Log video statistics to the hot tier (video_stats_log table).

        Args:
            stats_list: List of dicts with keys: video_id, views, likes, comment_count, timestamp
        """
        if not stats_list:
            return

        log_query = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """

        params_list = [
            (
                stat["video_id"],
                stat.get("views"),
                stat.get("likes"),
                stat.get("comment_count"),
                stat.get("timestamp", datetime.now(timezone.utc)),
            )
            for stat in stats_list
        ]

        await self._execute_many(log_query, params_list)
        logger.info(f"Logged {len(stats_list)} stats to hot tier")

    async def update_video_stats_batch(self, updates: List[Dict[str, Any]]) -> None:
        """Legacy method for updating video stats. Prefer log_video_stats_batch for new code."""
        timestamp_query = "UPDATE videos SET last_updated_at = %s WHERE id = %s"

        log_query = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """

        now = datetime.now(timezone.utc)

        async with self._cursor() as cur:
            for update in updates:
                vid = update["id"]
                stats = update.get("statistics", {})

                await cur.execute(timestamp_query, (now, vid))

                await cur.execute(
                    log_query,
                    (
                        vid,
                        stats.get("viewCount"),
                        stats.get("likeCount"),
                        stats.get("commentCount"),
                        now,
                    ),
                )

    async def fetch_scribe_batch(self, batch_size: int = 10) -> List[Dict[str, Any]]:
        """
        Fetch videos that need transcripts, prioritizing oldest first (FIFO).
        This ensures they are processed and vaulted before Janitor cleanup.
        """
        query = """
            SELECT id, title, published_at, discovered_at
            FROM videos
            WHERE status = 'PENDING'
              AND has_transcript = FALSE
            ORDER BY discovered_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        return await self._fetch_all(query, (batch_size,))

    async def fetch_painter_batch(self, batch_size: int = 5) -> List[Dict[str, Any]]:
        """
        Fetch videos that need visual processing, prioritizing oldest first (FIFO).
        This ensures they are processed and vaulted before Janitor cleanup.
        """
        query = """
            SELECT id, title, published_at, discovered_at
            FROM videos
            WHERE status = 'PENDING'
              AND has_visuals = FALSE
            ORDER BY discovered_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        return await self._fetch_all(query, (batch_size,))

    async def mark_video_transcript_safe(self, video_id: str) -> None:
        """
        Mark video transcript as safely stored in the Vault.
        This flag is checked by Janitor before cleanup.
        """
        query = """
            UPDATE videos
            SET has_transcript = TRUE,
                last_updated_at = %s
            WHERE id = %s
        """
        now = datetime.now(timezone.utc)
        await self._execute(query, (now, video_id))
        logger.info(f"Marked transcript safe for video {video_id}")

    async def mark_video_visuals_safe(self, video_id: str) -> None:
        """
        Mark video visuals as safely stored in the Vault.
        This flag is checked by Janitor before cleanup.
        """
        query = """
            UPDATE videos
            SET has_visuals = TRUE,
                last_updated_at = %s
            WHERE id = %s
        """
        now = datetime.now(timezone.utc)
        await self._execute(query, (now, video_id))
        logger.info(f"Marked visuals safe for video {video_id}")

    async def mark_video_done(self, video_id: str) -> None:
        """
        Mark video processing as complete.
        Videos with status='DONE' become eligible for Janitor cleanup after retention period.
        """
        query = """
            UPDATE videos
            SET status = 'DONE',
                last_updated_at = %s
            WHERE id = %s
        """
        now = datetime.now(timezone.utc)
        await self._execute(query, (now, video_id))

    async def mark_video_failed(self, video_id: str) -> None:
        """
        Mark video processing as failed.
        Failed videos are not cleaned up by Janitor.
        """
        query = """
            UPDATE videos
            SET status = 'FAILED',
                last_updated_at = %s
            WHERE id = %s
        """
        now = datetime.now(timezone.utc)
        await self._execute(query, (now, video_id))

    async def archive_cold_stats(self, retention_days: int = 7, batch_size: int = 5000) -> int:
        """Archive stats older than *retention_days* from hot tier (SQL) to cold tier (Vault).

        All database operations run inside a **single transaction**:

        1. ``SELECT … FOR UPDATE`` locks the target rows.
        2. Vault write pushes data to cold storage.
        3. ``DELETE`` removes the archived rows from SQL.

        If the Vault write raises, the transaction rolls back and no rows
        are deleted.  Returns the number of rows archived.
        """
        from collections import defaultdict

        from atlas.vault import get_vault

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

        async with self._connection() as conn:
            # Step 1: SELECT + lock rows within this transaction
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
                logger.info("No stats to archive")
                return 0

            # Step 2: Group by date for Vault
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

            # Step 3: Write to Vault — if this raises, the transaction rolls back
            v = get_vault()
            for date_str, day_stats in stats_by_date.items():
                v.append_metrics(day_stats, date=date_str)
                logger.info(f"Archived {len(day_stats)} stats for date {date_str}")

            # Step 4: DELETE only after Vault write succeeds (same transaction)
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

    async def run_janitor(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Clean up old processed videos from the hot queue.
        Only deletes videos that:
        1. Are older than JANITOR_RETENTION_DAYS
        2. Have status = 'DONE'
        3. Have both has_transcript AND has_visuals = TRUE (if safety check enabled)

        Returns statistics about the cleanup operation.
        """
        if not settings.JANITOR_ENABLED:
            logger.info("Janitor is disabled in settings")
            return {"deleted": 0, "reason": "disabled"}

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=settings.JANITOR_RETENTION_DAYS)

        # Build the WHERE clause based on safety settings
        safety_clause = ""
        if settings.JANITOR_SAFETY_CHECK:
            safety_clause = "AND (has_transcript = TRUE OR has_visuals = TRUE)"

        # First, count what would be deleted
        count_query = f"""
            SELECT COUNT(*) as total
            FROM videos
            WHERE discovered_at < %s
              AND status = 'DONE'
              {safety_clause}
        """

        count_result = await self._fetch_one(count_query, (cutoff_date,))
        total_to_delete = count_result["total"] if count_result else 0

        if total_to_delete == 0:
            logger.info("Janitor: No videos to clean up")
            return {"deleted": 0, "reason": "none_eligible"}

        if dry_run:
            logger.info(f"Janitor [DRY RUN]: Would delete {total_to_delete} videos")
            return {"deleted": 0, "dry_run": True, "would_delete": total_to_delete}

        # Perform the deletion
        delete_query = f"""
            DELETE FROM videos
            WHERE discovered_at < %s
              AND status = 'DONE'
              {safety_clause}
        """

        await self._execute(delete_query, (cutoff_date,))

        logger.info(
            f"Janitor: Cleaned up {total_to_delete} videos older than "
            f"{settings.JANITOR_RETENTION_DAYS} days"
        )

        return {
            "deleted": total_to_delete,
            "cutoff_date": cutoff_date.isoformat(),
            "retention_days": settings.JANITOR_RETENTION_DAYS,
            "safety_check_enabled": settings.JANITOR_SAFETY_CHECK,
        }
