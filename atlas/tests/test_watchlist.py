"""Unit tests for adaptive-scheduling decay tier logic (no DB)."""

from datetime import UTC, datetime, timedelta

import pytest
from atlas.repositories import WatchlistRepository


@pytest.fixture
def repo() -> WatchlistRepository:
    """WatchlistRepository with the default config thresholds."""
    return WatchlistRepository(db_pool=None)  # no DB access in these tests


@pytest.mark.parametrize(
    "age, expected_tier",
    [
        (timedelta(hours=2), "HOURLY"),
        (timedelta(hours=23), "HOURLY"),
        (timedelta(hours=24), "DAILY"),
        (timedelta(days=3), "DAILY"),
        (timedelta(days=6, hours=23), "DAILY"),
        (timedelta(days=7), "WEEKLY"),
        (timedelta(days=30), "WEEKLY"),
    ],
)
def test_age_floor(repo, age, expected_tier):
    published = datetime.now(UTC) - age
    tier, _next = repo.calculate_next_track_time(published_at=published)
    assert tier == expected_tier


def test_hot_velocity_promotes_old_video(repo):
    """A viral old video (velocity >= HOT) stays HOURLY past the age cutoffs."""
    published = datetime.now(UTC) - timedelta(days=30)
    tier, next_at = repo.calculate_next_track_time(
        published_at=published, views_per_hour=500.0
    )
    assert tier == "HOURLY"
    assert next_at <= datetime.now(UTC) + timedelta(hours=1, minutes=1)


def test_dead_velocity_drops_weekly_floor(repo):
    """A video with essentially no views/hour drops one tier, flooring at WEEKLY."""
    published = datetime.now(UTC) - timedelta(days=2)  # DAILY by age
    tier, next_at = repo.calculate_next_track_time(
        published_at=published, views_per_hour=0.0
    )
    assert tier == "WEEKLY"
    assert next_at <= datetime.now(UTC) + timedelta(days=7, hours=1)


def test_dead_hourly_drops_to_daily(repo):
    """A <24h video with zero velocity drops to DAILY (never slower than weekly)."""
    published = datetime.now(UTC) - timedelta(hours=10)  # HOURLY by age
    tier, next_at = repo.calculate_next_track_time(
        published_at=published, views_per_hour=0.0
    )
    assert tier == "DAILY"


def test_unknown_velocity_keeps_age_floor(repo):
    """Unknown velocity (None) falls back to the age floor, no boost or drop."""
    published = datetime.now(UTC) - timedelta(days=3)  # DAILY
    tier, _ = repo.calculate_next_track_time(published_at=published, views_per_hour=None)
    assert tier == "DAILY"


def test_no_published_at_uses_current_tier(repo):
    """After janitor deletes the videos row, published_at is NULL: keep current tier."""
    tier, next_at = repo.calculate_next_track_time(
        published_at=None, views_per_hour=None, tier="WEEKLY"
    )
    assert tier == "WEEKLY"
    assert next_at > datetime.now(UTC)
