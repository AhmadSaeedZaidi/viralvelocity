import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from atlas.adapters import DatabaseAdapter
from atlas.config import settings
from atlas.models.video import Video

if TYPE_CHECKING:
    from atlas.repositories.video.protocols import VideoRepositoryProtocol

logger = logging.getLogger("atlas.repositories.video.janitor")

_ARCHIVAL_BATCH_SIZE = 100

# A video is only safe to archive (drop from the hot tier) once its transcript
# content is confirmed stored in the vault. Otherwise the hand-off phase would
# DELETE the transcript row and zero has_* flags with the content never having
# left the hot DB — silently destroying it (and making the heartbeat report
# fabricated data loss). This clause excludes any video that:
#   - still has a vault flush pending (scheduler's vault_flush_task hasn't run), or
#   - has a transcript row whose content was never written to the vault.
_VAULT_SAFE_CLAUSE = """
  AND (vault_write_pending IS NOT TRUE)
  AND NOT EXISTS (
      SELECT 1 FROM transcripts t
      WHERE t.video_id = videos.id
        AND t.vault_uri IS NULL
  )
"""


class VideoJanitorMixin(DatabaseAdapter):
    async def sweep_archivable(self, batch_size: int = _ARCHIVAL_BATCH_SIZE) -> list[Video]:
        cutoff = datetime.now(UTC) - timedelta(days=settings.JANITOR_RETENTION_DAYS)
        rows = await self._fetch_all(
            f"""
            SELECT * FROM videos
            WHERE status = 'PROCESSED'
              AND last_updated_at < %s
              {_VAULT_SAFE_CLAUSE}
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
            f"""
            SELECT COUNT(*) as total FROM videos
            WHERE status = 'PROCESSED'
              AND last_updated_at < %s
              {_VAULT_SAFE_CLAUSE}
            """,
            (cutoff,),
        )
        return int(row["total"]) if row else 0

    async def count_videos(self) -> int:
        """Return the total number of videos in the corpus (fast estimate).

        Uses the planner's ``reltuples`` statistic so the query stays O(1) even
        as the corpus grows to millions of rows; falls back to an exact count
        when statistics are unavailable (e.g. a freshly-loaded table).
        """
        row = await self._fetch_one(
            "SELECT reltuples::bigint AS estimate FROM pg_class WHERE relname = 'videos'"
        )
        estimate = int(row["estimate"]) if row and row["estimate"] is not None else 0
        if estimate > 0:
            return estimate
        exact = await self._fetch_one("SELECT COUNT(*) AS total FROM videos")
        return int(exact["total"]) if exact else 0

    # ── Janitor: Hand-off Phase (serialize + vault + verify + purge) ──────

    async def archive_video_batch(
        self: "VideoRepositoryProtocol", videos: list[Video], dry_run: bool = False
    ) -> dict[str, Any]:
        """Serialize video metadata + stats to the vault, mark ARCHIVED, then purge.

        Vault writes run off the event loop via ``asyncio.to_thread``; the mark +
        purge happen in one transaction, and on vault failure the hot record is
        left untouched for retry.
        """
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

        # Write the whole sub-batch to the vault in a single commit to stay
        # within HuggingFace's 128-commits/hour limit.
        batch_items: list[tuple[str, dict[str, Any]]] = []
        date_keys: dict[str, str] = {}
        # Fetch the latest stats for the whole batch in ONE query (was one
        # ``get_latest_stats`` round-trip per video — the N+1 in the archive loop).
        latest_stats_map = await self.get_latest_stats_batch([v.id for v in videos])
        for video in videos:
            metadata: dict[str, Any] = {
                "id": video.id,
                "channel_id": video.channel_id,
                "title": video.title,
                "published_at": video.published_at.isoformat() if video.published_at else None,
                "duration": video.duration,
                "tags": video.tags,
                "category_id": video.category_id,
                "discovered_at": video.discovered_at.isoformat() if video.discovered_at else None,
                "last_updated_at": video.last_updated_at.isoformat()
                if video.last_updated_at
                else None,
                "has_transcript": video.has_transcript,
                "has_visuals": video.has_visuals,
            }

            latest_stats = latest_stats_map.get(video.id)
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
            date_keys[video.id] = date_key
            batch_items.append((f"metadata/{date_key}/{video.id}.json", metadata))

        try:
            await asyncio.to_thread(v.store_batch, batch_items)
        except Exception as exc:
            # The entire sub-batch failed to commit — every video is left
            # untouched in the hot DB so the janitor can retry next cycle.
            for video in videos:
                failed_count += 1
                failed_ids.append(video.id)
                logger.exception(f"Archival (vault batch) failed for {video.id}: {exc}")
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

        for video in videos:
            try:
                date_key = date_keys[video.id]

                if settings.JANITOR_SAFETY_CHECK:
                    verified = await asyncio.to_thread(v.fetch_metadata, video.id, date_key)
                    if not verified:
                        raise RuntimeError(
                            f"Vault verification failed for {video.id} — "
                            "metadata not found after write"
                        )

                now = datetime.now(UTC)
                # Defense-in-depth: never destroy transcript/stats/flag data that
                # hasn't been confirmed in the vault. The sweep gate normally
                # prevents these rows from ever being selected, but if a caller
                # bypasses it we must not silently lose content.
                unsafe = await self._fetch_one(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM transcripts t
                        WHERE t.video_id = %s AND t.vault_uri IS NULL
                    ) AS unvaulted
                    """,
                    (video.id,),
                )
                if unsafe and unsafe.get("unvaulted"):
                    raise RuntimeError(
                        f"Refusing to archive {video.id}: transcript not confirmed "
                        "in vault (vault_uri IS NULL)"
                    )
                async with self._connection() as conn, conn.transaction():
                    await conn.execute(
                        """
                            UPDATE videos
                            SET status = 'ARCHIVED',
                                archived_at = %s,
                                has_transcript = FALSE,
                                has_audio = FALSE,
                                has_visuals = FALSE
                            WHERE id = %s
                        """,
                        (now, video.id),
                    )
                    await conn.execute(
                        "DELETE FROM video_stats_log WHERE video_id = %s", (video.id,)
                    )
                    await conn.execute("DELETE FROM transcripts WHERE video_id = %s", (video.id,))

                archived_count += 1

            except Exception as exc:
                failed_count += 1
                failed_ids.append(video.id)
                logger.exception(f"Archival (verify/purge) failed for {video.id}: {exc}")
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

    async def archive_cold_stats(self, retention_days: int = 7, batch_size: int = 5000) -> int:
        """Move old ``video_stats_log`` rows to the vault and purge them from hot."""
        from atlas.vault import get_vault

        cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)

        async with self._connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                    SELECT video_id, views, likes, comment_count, timestamp
                    FROM video_stats_log
                    WHERE timestamp < %s
                    ORDER BY timestamp ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                (cutoff_date, batch_size),
            )
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchall()

        if not rows:
            return 0

        stats = [dict(zip(columns, row, strict=True)) for row in rows]

        stats_by_date: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        pairs: list[tuple[str, Any]] = []
        for stat in stats:
            ts = stat["timestamp"]
            date_str = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
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
            pairs.append((stat["video_id"], ts))

        # Step 2: upload to the vault off the event loop (no DB lock held).
        v = get_vault()
        await asyncio.to_thread(self._append_cold_metrics, v, stats_by_date)

        video_ids = [p[0] for p in pairs]
        timestamps = [p[1] for p in pairs]
        deleted = await self._delete_cold_stats(video_ids, timestamps)

        logger.info(f"Archived and purged {deleted} stats from hot tier")
        return deleted

    @staticmethod
    def _append_cold_metrics(vault: Any, stats_by_date: dict[str, list[dict[str, Any]]]) -> None:
        for date_str, day_stats in stats_by_date.items():
            vault.append_metrics(day_stats, date=date_str)

    async def _delete_cold_stats(self, video_ids: list[str], timestamps: list[Any]) -> int:
        # Pairwise unnest is a guaranteed zip on all PG versions.
        await self._execute(
            """
            DELETE FROM video_stats_log
            WHERE (video_id, timestamp) IN (
                SELECT * FROM unnest(%s::text[], %s::timestamptz[]) AS t(video_id, timestamp)
            )
            """,
            (video_ids, timestamps),
        )
        return len(video_ids)

    # ── Hard delete of fully-archived videos past retention ───────────────

    async def run_janitor(self, dry_run: bool = False) -> dict[str, Any]:
        if not settings.JANITOR_ENABLED:
            return {"deleted": 0, "reason": "disabled"}

        cutoff_date = datetime.now(UTC) - timedelta(days=settings.JANITOR_RETENTION_DAYS)
        safety_clause = ""
        if settings.JANITOR_SAFETY_CHECK:
            safety_clause = "AND (has_transcript = TRUE OR has_visuals = TRUE)"

        # Only PROCESSED and ARCHIVED rows are eligible for hard deletion.
        count_result = await self._fetch_one(
            f"""
            SELECT COUNT(*) as total
            FROM videos
            WHERE discovered_at < %s
              AND status IN ('PROCESSED', 'ARCHIVED')
              {safety_clause}
            """,
            (cutoff_date,),
        )
        total_to_delete = int(count_result["total"]) if count_result else 0

        if total_to_delete == 0:
            return {"deleted": 0, "reason": "none_eligible"}

        if dry_run:
            return {"deleted": 0, "dry_run": True, "would_delete": total_to_delete}

        await self._execute(
            f"""
            DELETE FROM videos
            WHERE discovered_at < %s
              AND status IN ('PROCESSED', 'ARCHIVED')
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
