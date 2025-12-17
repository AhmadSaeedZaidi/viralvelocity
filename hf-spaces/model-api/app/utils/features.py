from typing import List

import numpy as np


def calculate_engagement_score(likes: int, comments: int, views: int) -> float:
    """
    Calculates the engagement score based on the formula:
    (Likes + Comments * 2) / Views
    """
    if views <= 0:
        return 0.0
    return (likes + (comments * 2)) / views


def preprocess_text_features(title: str, tags: List[str]) -> str:
    """
    Combines title and tags into a single string for TF-IDF vectorization.
    """
    tag_str = " ".join(tags)
    return f"{title} {tag_str}".lower().strip()


def calculate_rank_velocity(rank_history: List[int]) -> float:
    """
    Calculates the derivative (rate of change) of the rank.
    Positive value = Dropping in rank (bad)
    Negative value = Climbing in rank (good, as 1 is top)
    """
    if len(rank_history) < 2:
        return 0.0
    # Simple slope calculation between first and last point
    return (rank_history[-1] - rank_history[0]) / len(rank_history)


def encode_time_features(hour: int, day_of_week: int) -> np.ndarray:
    """
    Example of transforming time into cyclical features (sin/cos)
    if you want to upgrade your models later.
    """
    # This is a placeholder for future advanced feature engineering
    return np.array([hour, day_of_week])
