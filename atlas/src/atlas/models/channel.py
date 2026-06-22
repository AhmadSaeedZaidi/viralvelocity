from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class Channel(BaseModel):
    id: str
    title: str
    country: Optional[str] = None
    custom_url: Optional[str] = None
    created_at: Optional[datetime] = None
    is_verified: bool = False
    last_scraped_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ChannelStats(BaseModel):
    channel_id: str
    timestamp: datetime
    view_count: Optional[int] = None
    subscriber_count: Optional[int] = None
    video_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ChannelHistory(BaseModel):
    id: str
    channel_id: str
    changed_at: Optional[datetime] = None
    old_title: Optional[str] = None
    new_title: Optional[str] = None
    event_type: str

    model_config = ConfigDict(from_attributes=True)
