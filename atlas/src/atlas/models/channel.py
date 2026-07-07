from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Channel(BaseModel):
    id: str
    title: str
    country: str | None = None
    custom_url: str | None = None
    created_at: datetime | None = None
    is_verified: bool = False
    last_scraped_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ChannelStats(BaseModel):
    channel_id: str
    timestamp: datetime
    view_count: int | None = None
    subscriber_count: int | None = None
    video_count: int | None = None

    model_config = ConfigDict(from_attributes=True)


class ChannelHistory(BaseModel):
    id: str
    channel_id: str
    changed_at: datetime | None = None
    old_title: str | None = None
    new_title: str | None = None
    event_type: str

    model_config = ConfigDict(from_attributes=True)
