from typing import List

from pydantic import BaseModel


class VideoStats(BaseModel):
    view_count: int
    like_count: int
    comment_count: int
    duration_seconds: int
    published_hour: int
    published_day_of_week: int


class ChannelStats(BaseModel):
    id: str
    avg_views_last_5: float
    subscriber_count: int


class VelocityInput(BaseModel):
    video_stats_24h: VideoStats
    channel_stats: ChannelStats
    slope_views: float
    slope_engagement: float


class ClickbaitInput(BaseModel):
    title: str
    view_count: int
    like_count: int
    comment_count: int
    publish_hour: int = 0
    publish_day: int = 0
    is_weekend: int = 0


class GenreInput(BaseModel):
    title: str
    tags: List[str]


class TagInput(BaseModel):
    current_tags: List[str]


class ViralInput(BaseModel):
    discovery_rank_history: List[int]
    rank_velocity: float


class AnomalyInput(BaseModel):
    view_count: int
    like_count: int
    comment_count: int
    duration_seconds: int
