from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

VideoStatus = Literal["PENDING", "PROCESSING", "PROCESSED", "ARCHIVED", "FAILED"]


class Video(BaseModel):
    id: str
    channel_id: Optional[str] = None
    title: str
    published_at: Optional[datetime] = None
    duration: Optional[int] = None
    tags: Optional[list[str]] = None
    category_id: Optional[str] = None
    default_language: Optional[str] = None
    wiki_topics: Optional[list[str]] = None
    discovered_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    status: Optional[str] = "PENDING"
    has_transcript: bool = False
    has_visuals: bool = False

    model_config = ConfigDict(from_attributes=True)


class VideoStats(BaseModel):
    video_id: str
    timestamp: datetime
    views: Optional[int] = None
    likes: Optional[int] = None
    comment_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)
