from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

VideoStatus = Literal["PENDING", "PROCESSING", "PROCESSED", "ARCHIVED", "FAILED"]


class Video(BaseModel):
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

    model_config = ConfigDict(from_attributes=True)


class VideoStats(BaseModel):
    video_id: str
    timestamp: datetime
    views: int | None = None
    likes: int | None = None
    comment_count: int | None = None

    model_config = ConfigDict(from_attributes=True)
