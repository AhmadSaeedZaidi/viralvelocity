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
    # Core Features
    log_start_views: float
    log_duration: float
    initial_virality_slope: float
    interaction_density: float

    # Ratios
    like_view_ratio: float
    comment_view_ratio: float

    # Temporal
    video_age_hours: float
    hour_sin: float
    hour_cos: float
    publish_day: int
    is_weekend: int

    # Text
    title_len: int
    caps_ratio: float
    exclamation_count: int
    question_count: int
    has_digits: int

    # Category
    category_id: int = -1


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
    # Core metrics
    view_velocity: float
    like_velocity: float
    comment_velocity: float

    # Ratios & Log features
    like_ratio: float
    comment_ratio: float
    log_start_views: float

    # Temporal & Meta
    video_age_hours: float
    duration_seconds: int
    hour_sin: float
    hour_cos: float

    # Advanced Features
    initial_virality_slope: float
    interaction_density: float

    # Text Features
    title_len: int
    caps_ratio: float
    has_digits: int


class AnomalyInput(BaseModel):
    view_count: int
    like_count: int
    comment_count: int
    duration_seconds: int
