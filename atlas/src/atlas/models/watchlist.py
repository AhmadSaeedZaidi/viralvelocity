from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WatchlistItem(BaseModel):  # type: ignore[misc]
    video_id: str
    tracking_tier: str = "HOURLY"
    last_tracked_at: datetime | None = None
    next_track_at: datetime
    created_at: datetime | None = None
    # Joined from videos for decay scheduling (may be NULL after janitor
    # hard-deletes the video row; age-tier then falls back to created_at).
    published_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
