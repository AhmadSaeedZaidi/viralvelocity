import asyncio
import logging
from datetime import UTC, datetime

from atlas.adapters import DatabaseAdapter
from atlas.models.video import Video

logger = logging.getLogger("atlas.repositories.video.state_machine")


class VideoStateMixin(DatabaseAdapter):
    # ── State Machine: Claim methods (atomic PENDING → PROCESSING) ────────

    async def claim_scribe_batch(self, batch_size: int = 10) -> list[Video]:
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING', transcript_phase = 'PROCESSING'
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
        # Painter is now a *consumer*: it reads the raw artifact the unified
        # streamer stored in the vault, so it only processes videos that have
        # already been fetched (fetched = TRUE).
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING', visuals_phase = 'PROCESSING'
            WHERE id IN (
                SELECT id FROM videos
                WHERE status IN ('PENDING', 'PROCESSING')
                  AND fetched = TRUE
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

    async def claim_streamer_batch(self, batch_size: int = 5) -> list[Video]:
        """Claim videos whose YouTube source has NOT yet been fetched.

        The streamer does the network pull only; the singer later extracts the
        audio from the stored raw artifact.
        """
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING', raw_phase = 'PROCESSING'
            WHERE id IN (
                SELECT id FROM videos
                WHERE status IN ('PENDING', 'PROCESSING')
                  AND fetched = FALSE
                ORDER BY discovered_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (batch_size,),
        )
        return [Video.model_validate(r) for r in rows]

    async def claim_singer_batch(self, batch_size: int = 5) -> list[Video]:
        """Claim videos that have been fetched but whose audio is not stored yet.

        The singer fetches the raw artifact, extracts the speech track locally
        (no YouTube rate limit), stores ``audio/{id}.opus``, and flips
        ``has_audio``.
        """
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING', audio_phase = 'PROCESSING'
            WHERE id IN (
                SELECT id FROM videos
                WHERE status IN ('PENDING', 'PROCESSING', 'PROCESSED')
                  AND fetched = TRUE
                  AND has_audio = FALSE
                ORDER BY discovered_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (batch_size,),
        )
        return [Video.model_validate(r) for r in rows]

    async def claim_muralist_batch(self, batch_size: int = 5) -> list[Video]:
        rows = await self._fetch_all(
            """
            UPDATE videos SET status = 'PROCESSING', clip_phase = 'PROCESSING'
            WHERE id IN (
                SELECT id FROM videos
                WHERE status IN ('PENDING', 'PROCESSING')
                  AND has_video = FALSE
                  AND raw_uri IS NOT NULL
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
                -- PROCESSED requires the full audio+visuals+transcript set; do not
                -- latch it until audio is also present (scribe is caption-first and
                -- runs in parallel with the singer, so audio may finish after here).
                status = CASE WHEN has_visuals AND has_audio THEN 'PROCESSED' ELSE status END
            WHERE id = %s AND transcript_phase <> 'DONE'
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
                -- PROCESSED requires the full audio+visuals+transcript set; do not
                -- latch it until audio is also present (the singer may finish after
                -- the painter, so audio is latched separately in mark_audio_safe).
                status = CASE WHEN has_transcript AND has_audio THEN 'PROCESSED' ELSE status END
            WHERE id = %s AND visuals_phase <> 'DONE'
            """,
            (now, video_id),
        )

    async def mark_fetched(
        self,
        video_id: str,
        raw_uri: str,
    ) -> None:
        """Record that the YouTube source was fetched and stored at *raw_uri*."""
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET fetched = TRUE,
                raw_uri = %s,
                raw_stored_at = %s,
                last_updated_at = %s
            WHERE id = %s AND raw_phase <> 'DONE'
            """,
            (raw_uri, now, now, video_id),
        )

    async def reclaim_raw_if_complete(self, video_id: str) -> int:
        """Delete the raw artifact once every raw-consuming step has joined.

        This is the **join barrier** of the ``raw → {singer, painter, muralist}``
        fan-out (see proposals docs). The raw is only safe to reclaim once the
        parallel consumers that read it are all ``DONE`` — ``audio`` (singer) and
        ``visuals`` (painter) are mandatory; ``clip`` (muralist) is mandatory too
        *unless* the muralist is manual-only and the raw has aged past
        ``RAW_TTL_HOURS`` (so disk stays bounded). Returns files reclaimed (0/1).
        """
        from atlas.config import settings

        row = await self._fetch_one(
            """
            SELECT raw_uri, audio_phase, visuals_phase, clip_phase, raw_stored_at
            FROM videos WHERE id = %s
            """,
            (video_id,),
        )
        # Join barrier: reclaim only once BOTH mandatory derived consumers are
        # DONE. Requiring `audio` (singer) + `visuals` (painter) before touching
        # the raw prevents reclaiming the input out from under a still-pending
        # consumer — the root cause of the muralist starvation bug.
        if not row or not row["raw_uri"]:
            return 0
        if not (row["audio_phase"] == "DONE" and row["visuals_phase"] == "DONE"):
            return 0

        # Keep the raw bounded when the muralist (clip producer) is disabled or
        # manual-only: reclaim as soon as the clip is DONE, otherwise only after
        # the raw has aged past RAW_TTL_HOURS. A NULL raw_stored_at (rows that
        # predate this column) is never reclaimed via age — only via clip DONE.
        clip_done = row["clip_phase"] == "DONE"
        raw_stored_at = row["raw_stored_at"]
        raw_age_hours = (
            (datetime.now(UTC) - raw_stored_at).total_seconds() / 3600.0
            if raw_stored_at is not None
            else None
        )
        ttl_hours = settings.RAW_TTL_HOURS
        if not clip_done and not (raw_age_hours is not None and raw_age_hours > ttl_hours):
            return 0

        from atlas.vault import get_vault, meta_path

        paths = [row["raw_uri"], meta_path(video_id)]
        try:
            deleted = await asyncio.to_thread(get_vault().delete_files, paths)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"raw reclamation failed for {video_id}: {e}")
            return 0
        # Clear the pointer so we never try to reclaim twice.
        await self._execute("UPDATE videos SET raw_uri = NULL WHERE id = %s", (video_id,))
        logger.info(f"Reclaimed {deleted} raw artifacts for {video_id}")
        return deleted

    # ── P1b per-step state helpers (fan-out / fan-in) ──────────────────────

    _STEP_COLUMNS = frozenset({"raw", "audio", "visuals", "transcript", "clip"})

    async def begin_step(
        self, video_id: str, step: str, phase: str = "PROCESSING"
    ) -> None:
        """Mark a step as in-progress. Idempotent: a DONE step is never
        downgraded back to PROCESSING (so a re-claim of a finished row cannot
        clobber its completed state)."""
        await self.mark_step_phase(video_id, step, phase)

    async def mark_step_phase(self, video_id: str, step: str, phase: str) -> None:
        """Set a single step's phase column (PENDING/PROCESSING/DONE/FAILED).

        The ``sync_step_phases`` trigger keeps the legacy boolean in sync, so
        callers may drive either the phase or the boolean. Unknown step names
        raise ``ValueError``.
        """
        if step not in self._STEP_COLUMNS:
            raise ValueError(f"Unknown pipeline step: {step!r}")
        now = datetime.now(UTC)
        await self._execute(
            f"UPDATE videos SET {step}_phase = %s::step_phase, last_updated_at = %s "
            f"WHERE id = %s AND {step}_phase <> 'DONE'",
            (phase, now, video_id),
        )

    async def get_pipeline_phase(self, video_id: str) -> str | None:
        """Return the derived frontier (RAW/AUDIO/VISUALS/TRANSCRIPT/CLIP/DONE)."""
        row = await self._fetch_one(
            "SELECT pipeline_phase FROM videos WHERE id = %s", (video_id,)
        )
        return row["pipeline_phase"] if row else None

    async def mark_audio_safe(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET has_audio = TRUE,
                last_updated_at = %s,
                -- If audio was the last missing artifact, latch PROCESSED now. This
                -- closes the fan-out race: scribe/painter may have already set
                -- has_transcript/has_visuals but could not latch PROCESSED without audio.
                status = CASE WHEN has_visuals AND has_transcript THEN 'PROCESSED' ELSE status END
            WHERE id = %s AND audio_phase <> 'DONE'
            """,
            (now, video_id),
        )

    async def mark_video_safe(self, video_id: str) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET has_video = TRUE,
                last_updated_at = %s
            WHERE id = %s AND clip_phase <> 'DONE'
            """,
            (now, video_id),
        )

    async def repaint_all_videos(self) -> int:
        """Reset every video so its visual evidence is recollected.

        Sets ``status='PENDING'`` and ``has_visuals=FALSE`` for all rows so the
        painter re-claims and re-archives frames on its next cycle. Used after a
        corruption fix to force re-ingestion of already-stored (bad) frames.
        """
        now = datetime.now(UTC)
        rows = await self._fetch_all(
            """
            UPDATE videos
            SET status = 'PENDING', has_visuals = FALSE, last_updated_at = %s
            RETURNING id
            """,
            (now,),
        )
        return len(rows)

    async def reset_failed_to_pending(self) -> int:
        """Release every ``FAILED`` video back to ``PENDING`` for reprocessing.

        Also clears ``has_visuals`` so the Painter re-claims and re-collects
        (and overwrites) any bad frames left in the vault. Used to recover from
        a run of failed cycles (e.g. the frame-corruption bug, transient quota
        exhaustion) reported by the heartbeat as "FAILED: N".
        """
        now = datetime.now(UTC)
        rows = await self._fetch_all(
            """
            UPDATE videos
            SET status = 'PENDING', has_visuals = FALSE, last_updated_at = %s
            WHERE status = 'FAILED'
            RETURNING id
            """,
            (now,),
        )
        return len(rows)

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

    async def release_to_pending(self, video_id: str) -> None:
        """Release a claimed (PROCESSING) video back to PENDING for retry.

        Used for transient failures (e.g. rate limiting) so the video is
        re-claimed on a later cycle instead of being marked done or failed.
        """
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET status = 'PENDING', last_updated_at = %s
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

    async def unmark_transcript(self, video_id: str) -> None:
        """Revert a video to needing a transcript so the Scribe re-extracts it.

        Used by quality-control audits: a video whose stored transcript/audio is
        found corrupt is unchecked (``has_transcript=FALSE``) and returned to
        ``PENDING`` so the Scribe re-claims and re-extracts it. ``has_visuals``
        is left untouched so the Painter does not needlessly re-run.
        """
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET has_transcript = FALSE, status = 'PENDING', last_updated_at = %s
            WHERE id = %s
            """,
            (now, video_id),
        )

    async def find_transcript_video_ids(self, scope: str = "without_visuals") -> list[str]:
        """Return IDs of videos whose transcript should be reset, by *scope*.

        Scopes:
          - ``all``            : every video with a transcript (``has_transcript``).
          - ``without_visuals``: transcript present but no stored visuals.
          - ``without_audio``  : transcript present but no stored audio.
          - ``pending``        : transcript present but still ``PENDING`` (unvetted).
        """
        if scope == "all":
            where = "has_transcript = TRUE"
        elif scope == "without_visuals":
            where = "has_transcript = TRUE AND has_visuals = FALSE"
        elif scope == "without_audio":
            where = "has_transcript = TRUE AND has_audio = FALSE"
        elif scope == "pending":
            where = "has_transcript = TRUE AND status = 'PENDING'"
        else:
            raise ValueError(f"Unknown transcript purge scope: {scope!r}")
        rows = await self._fetch_all(f"SELECT id FROM videos WHERE {where}")
        return [r["id"] for r in rows]

    async def unmark_transcripts_batch(self, video_ids: list[str]) -> int:
        """Batch-uncheck transcripts so the Scribe cleanly re-derives them.

        Implements the "uncheck / mark-as-unprocessed + organic overwrite" reset:
        each video is returned to ``PENDING`` and ``has_transcript`` cleared, but
        the row is NOT deleted. When the Scribe re-claims it, ``record_transcript``
        upserts (``ON CONFLICT DO UPDATE``) — overwriting the stale content in place
        rather than a fragile delete-then-insert. The stale ``vault_write_pending``
        flag is cleared too, so the janitor does not flush the old content.
        """
        if not video_ids:
            return 0
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET has_transcript = FALSE,
                status = 'PENDING',
                vault_write_pending = FALSE,
                last_updated_at = %s
            WHERE id = ANY(%s)
            """,
            (now, video_ids),
        )
        return len(video_ids)

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
