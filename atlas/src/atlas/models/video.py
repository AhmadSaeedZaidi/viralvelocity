from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

VideoStatus = Literal["PENDING", "PROCESSING", "PROCESSED", "ARCHIVED", "FAILED"]

# Per-step state for the fan-out / fan-in pipeline (P1b). Each derived artifact
# has its own phase so a video's progress reads as a state, not a conjunction of
# booleans. `raw` is the fan-out source produced by the streamer; `audio`
# (singer), `visuals` (painter), `transcript` (scribe) and `clip` (muralist) are
# the consumers. The legacy booleans remain as a transitional seam (kept in sync
# by the `sync_step_phases` trigger) until P3.
StepPhase = Literal["PENDING", "PROCESSING", "DONE", "FAILED"]


class Video(BaseModel):  # type: ignore[misc]
    id: str
    channel_id: str | None = None
    title: str
    published_at: datetime | None = None
    duration: int | None = None
    tags: list[str] | None = None
    category_id: str | None = None
    default_language: str | None = None
    wiki_topics: list[str] | None = None
    discovered_at: datetime | None = None
    last_updated_at: datetime | None = None
    archived_at: datetime | None = None
    status: str | None = "PENDING"
    has_transcript: bool = False
    has_visuals: bool = False
    has_audio: bool = False
    has_video: bool = False
    fetched: bool = False
    raw_uri: str | None = None
    # ── P1b per-step phases ──
    raw_phase: StepPhase = "PENDING"
    audio_phase: StepPhase = "PENDING"
    visuals_phase: StepPhase = "PENDING"
    transcript_phase: StepPhase = "PENDING"
    clip_phase: StepPhase = "PENDING"
    # Derived frontier (see `pipeline_frontier` in schema.sql); queryable for
    # ops/monitoring, never drives claim selection.
    pipeline_phase: str | None = None

    model_config = ConfigDict(from_attributes=True)


class VideoStats(BaseModel):  # type: ignore[misc]
    video_id: str
    timestamp: datetime
    views: int | None = None
    likes: int | None = None
    comment_count: int | None = None

    model_config = ConfigDict(from_attributes=True)
