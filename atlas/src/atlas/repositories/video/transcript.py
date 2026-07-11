"""Transcript + vault-pending repository (Single Responsibility).

Previously these operations lived on ``VideoStateMixin`` alongside the video
state machine. They form a distinct domain — staging transcripts locally and
tracking which ones still need flushing to the vault — so they are extracted
into their own repository per the DAO/repository pattern.
"""

import json
from datetime import UTC, datetime
from typing import Any

from atlas.adapters import DatabaseAdapter


class TranscriptRepository(DatabaseAdapter):
    """DAO for transcript staging and vault-write pending tracking (Option A)."""

    async def record_transcript(
        self,
        video_id: str,
        vault_uri: str | None,
        language: str = "en",
        content_json: Any | None = None,
        audio_bytes: bytes | None = None,
    ) -> None:
        content_param: Any = json.dumps(content_json) if content_json is not None else None
        """Record a transcript locally (staging) and queue the vault write.

        Under the janitor-owned persistence model (Option A), the scribe only
        stages: it inserts the transcript row and (optionally) the extracted
        audio bytes, then marks ``vault_write_pending`` so the janitor flushes
        everything to the vault in batched commits. ``vault_uri`` stays NULL
        until that flush succeeds. Pass ``vault_uri`` only for the janitor's
        post-flush update (which also clears the pending flag).
        """
        async with self._cursor() as cur:
            await cur.execute(
                """
                INSERT INTO transcripts (video_id, language, vault_uri, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (video_id) DO UPDATE
                    SET vault_uri = EXCLUDED.vault_uri,
                        language = EXCLUDED.language,
                        content = EXCLUDED.content
                """,
                (video_id, language, vault_uri, content_param),
            )
            if audio_bytes is not None:
                await cur.execute(
                    "UPDATE videos SET audio_pending = %s WHERE id = %s",
                    (audio_bytes, video_id),
                )
            if vault_uri is None:
                await cur.execute(
                    "UPDATE videos SET vault_write_pending = TRUE WHERE id = %s",
                    (video_id,),
                )
            await cur.connection.commit()

    async def claim_vault_pending_batch(self, batch_size: int = 50) -> list[dict[str, Any]]:
        """Claim videos whose transcript/audio still need flushing to the vault.

        Returns lightweight dicts (id, transcript JSON, audio bytes) for the
        janitor to batch-write. Idempotent + concurrency-safe via SKIP LOCKED.
        """
        rows = await self._fetch_all(
            """
            SELECT v.id,
                   t.language,
                   t.content AS transcript,
                   v.audio_pending AS audio
            FROM videos v
            JOIN transcripts t ON t.video_id = v.id
            WHERE v.has_transcript
              AND (v.vault_write_pending OR t.vault_uri IS NULL)
            ORDER BY v.discovered_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (batch_size,),
        )
        return [dict(r) for r in rows]

    async def clear_vault_pending(self, video_id: str, vault_uri: str) -> None:
        """Mark a video's vault write complete (called by the janitor)."""
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE videos
            SET vault_write_pending = FALSE,
                audio_pending = NULL,
                last_updated_at = %s,
                status = CASE WHEN has_visuals THEN 'PROCESSED' ELSE status END
            WHERE id = %s
            """,
            (now, video_id),
        )
        await self._execute(
            "UPDATE transcripts SET vault_uri = %s WHERE video_id = %s",
            (vault_uri, video_id),
        )
