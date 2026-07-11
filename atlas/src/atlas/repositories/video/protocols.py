"""Shared typing protocol for the composed ``VideoRepository``.

The repository is assembled from several focused mixins (ingestion, tracking,
state machine, janitor). Some mixins call methods that are defined on a
*sibling* mixin — those methods only exist once everything is composed into
``VideoRepository``. To keep ``mypy --strict`` happy without runtime changes,
mixins that call sibling methods type ``self`` as :class:`VideoRepositoryProtocol`,
which models the full public surface of the composed repository.

This makes cross-mixin dependencies explicit and self-documenting instead of
relying on scattered ``TYPE_CHECKING`` stubs.
"""

from typing import Any, Protocol

from atlas.adapters import DatabaseAdapterProtocol
from atlas.models.video import Video, VideoStats


class VideoRepositoryProtocol(DatabaseAdapterProtocol, Protocol):
    """Full public interface of the composed ``VideoRepository``.

    Declares every method a mixin may invoke on ``self`` across mixin
    boundaries. Extends :class:`DatabaseAdapterProtocol` so the low-level
    ``_execute`` / ``_fetch_*`` helpers are also visible.
    """

    # ── Ingestion ─────────────────────────────────────────────────────────
    async def save(self, video: Video) -> None: ...

    async def get_by_id(self, video_id: str) -> Video | None: ...

    async def get_latest_stats(self, video_id: str) -> VideoStats | None: ...

    async def ingest_video_metadata(
        self, video_data: dict[str, Any], priority_override: int | None = None
    ) -> None: ...

    # ── Tracking ──────────────────────────────────────────────────────────
    async def fetch_tracker_targets(self, batch_size: int = 50) -> list[Video]: ...

    async def log_stats_batch(self, stats_list: list[VideoStats]) -> None: ...

    async def update_stats_batch(self, updates: list[dict[str, Any]]) -> None: ...

    # ── State machine ─────────────────────────────────────────────────────
    async def claim_scribe_batch(self, batch_size: int = 10) -> list[Video]: ...

    async def claim_painter_batch(self, batch_size: int = 5) -> list[Video]: ...

    async def claim_streamer_batch(self, batch_size: int = 5) -> list[Video]: ...

    async def claim_singer_batch(self, batch_size: int = 5) -> list[Video]: ...

    async def claim_muralist_batch(self, batch_size: int = 5) -> list[Video]: ...

    async def mark_transcript_safe(self, video_id: str) -> None: ...

    async def mark_visuals_safe(self, video_id: str) -> None: ...

    async def mark_fetched(
        self, video_id: str, raw_uri: str,
    ) -> None: ...

    async def mark_audio_safe(self, video_id: str) -> None: ...

    async def mark_video_safe(self, video_id: str) -> None: ...

    async def mark_done(self, video_id: str) -> None: ...

    async def record_transcript(
        self, video_id: str, vault_uri: str, language: str = "en"
    ) -> None: ...

    async def release_to_pending(self, video_id: str) -> None: ...

    async def mark_failed(self, video_id: str) -> None: ...

    async def mark_archived(self, video_id: str) -> None: ...

    async def find_transcript_video_ids(self, scope: str = "without_visuals") -> list[str]: ...

    async def unmark_transcripts_batch(self, video_ids: list[str]) -> int: ...
