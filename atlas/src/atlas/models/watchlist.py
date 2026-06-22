from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class WatchlistItem(BaseModel):
    video_id: str
    tracking_tier: str = "HOURLY"
    last_tracked_at: Optional[datetime] = None
    next_track_at: datetime
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
