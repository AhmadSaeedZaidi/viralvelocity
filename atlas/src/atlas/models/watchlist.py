from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WatchlistItem(BaseModel):
    video_id: str
    tracking_tier: str = "HOURLY"
    last_tracked_at: datetime | None = None
    next_track_at: datetime
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
