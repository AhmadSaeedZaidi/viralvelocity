"""
Tests for Maia Tracker module (adaptive-scheduling watchlist).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from atlas.utils import QuotaExhaustedError
from maia.tracker.flow import fetch_targets_task, update_stats_task


@pytest.fixture
def mock_strategy() -> MagicMock:
    strategy = MagicMock()
    strategy.fetch_videos = AsyncMock()
    return strategy


@pytest.mark.asyncio
async def test_fetch_targets_empty():
    """Test fetch_targets when no videos need updates."""
    with patch("maia.tracker.flow.WatchlistRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.fetch_batch = AsyncMock(return_value=[])

        result = await fetch_targets_task.fn(batch_size=50)

        assert result == []
        mock_repo.fetch_batch.assert_called_once_with(50)


@pytest.mark.asyncio
async def test_fetch_targets_with_videos(mock_watchlist_item: dict[str, Any]):
    """Test fetch_targets returns watchlist items needing updates."""
    from atlas.models import WatchlistItem

    with patch("maia.tracker.flow.WatchlistRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.fetch_batch = AsyncMock(
            return_value=[WatchlistItem(**mock_watchlist_item)]
        )

        result = await fetch_targets_task.fn(batch_size=50)

        assert len(result) == 1
        assert result[0]["video_id"] == "dQw4w9WgXcQ"


@pytest.mark.asyncio
async def test_update_stats_empty_list(mock_strategy: MagicMock):
    """Test update_stats with empty video list."""
    result = await update_stats_task.fn([], mock_strategy)
    assert result == 0


@pytest.mark.asyncio
async def test_update_stats_success(
    mock_strategy: MagicMock, mock_youtube_stats_response: dict[str, Any]
):
    """Test update_stats fetches stats and advances the decay schedule."""
    mock_strategy.fetch_videos.return_value = mock_youtube_stats_response

    mock_videos = [{"video_id": "dQw4w9WgXcQ", "tracking_tier": "HOURLY"}]

    with (
        patch("maia.tracker.flow.VideoRepository") as MockVideoRepo,
        patch("maia.tracker.flow.WatchlistRepository") as MockWatchRepo,
    ):
        mock_video_repo = MockVideoRepo.return_value
        mock_video_repo.update_stats_batch = AsyncMock()
        mock_watch = MockWatchRepo.return_value
        mock_watch.velocity_views_per_hour = AsyncMock(return_value={})
        mock_watch.calculate_next_track_time = MagicMock(return_value=("DAILY", 1))
        mock_watch.update_schedule = AsyncMock()

        result = await update_stats_task.fn(mock_videos, mock_strategy)

        assert result == 1
        mock_video_repo.update_stats_batch.assert_called_once()
        mock_watch.update_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_stats_schedules_missing_videos(
    mock_strategy: MagicMock, mock_youtube_stats_response: dict[str, Any]
):
    """Test update_stats advances the schedule for videos the API no longer
    returns (deleted/private/geo-blocked)."""
    returned_id = mock_youtube_stats_response["items"][0]["id"]
    dead_videos = [{"video_id": "dead_video_1"}, {"video_id": "dead_video_2"}]
    mock_strategy.fetch_videos.return_value = mock_youtube_stats_response

    with (
        patch("maia.tracker.flow.VideoRepository") as MockVideoRepo,
        patch("maia.tracker.flow.WatchlistRepository") as MockWatchRepo,
    ):
        mock_video_repo = MockVideoRepo.return_value
        mock_video_repo.update_stats_batch = AsyncMock()
        mock_watch = MockWatchRepo.return_value
        mock_watch.velocity_views_per_hour = AsyncMock(return_value={})
        mock_watch.calculate_next_track_time = MagicMock(return_value=("WEEKLY", 2))
        mock_watch.update_schedule = AsyncMock()

        result = await update_stats_task.fn(
            dead_videos + [{"video_id": returned_id}], mock_strategy
        )

        assert result == 1
        # Both dead and live videos get a schedule update.
        mock_watch.update_schedule.assert_awaited_once()
        updates = mock_watch.update_schedule.call_args.args[0]
        assert {u["video_id"] for u in updates} == {
            "dead_video_1",
            "dead_video_2",
            returned_id,
        }
        mock_video_repo.update_stats_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_stats_empty_items_does_not_log(
    mock_strategy: MagicMock, mock_tracker_target: dict[str, Any]
):
    """Test update_stats returns 0 and skips stats logging when the API returns
    no items (all videos gone from YouTube), but still advances schedules."""
    mock_strategy.fetch_videos.return_value = {"items": []}

    with (
        patch("maia.tracker.flow.VideoRepository") as MockVideoRepo,
        patch("maia.tracker.flow.WatchlistRepository") as MockWatchRepo,
    ):
        mock_video_repo = MockVideoRepo.return_value
        mock_video_repo.update_stats_batch = AsyncMock()
        mock_watch = MockWatchRepo.return_value
        mock_watch.velocity_views_per_hour = AsyncMock(return_value={})
        mock_watch.calculate_next_track_time = MagicMock(return_value=("WEEKLY", 0))
        mock_watch.update_schedule = AsyncMock()

        result = await update_stats_task.fn([mock_tracker_target], mock_strategy)

        assert result == 0
        mock_video_repo.update_stats_batch.assert_not_awaited()
        mock_watch.update_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_stats_handles_api_errors(
    mock_strategy: MagicMock, mock_tracker_target: dict[str, Any]
):
    """Test update_stats handles API errors gracefully."""
    mock_strategy.fetch_videos.side_effect = Exception("API Error")

    with (
        patch("maia.tracker.flow.VideoRepository"),
        patch("maia.tracker.flow.WatchlistRepository"),
    ):
        result = await update_stats_task.fn([mock_tracker_target], mock_strategy)

        assert result == 0


@pytest.mark.asyncio
async def test_update_stats_propagates_rate_limit(
    mock_strategy: MagicMock, mock_tracker_target: dict[str, Any]
):
    """Test update_stats propagates QuotaExhaustedError."""

    mock_strategy.fetch_videos.side_effect = QuotaExhaustedError("All keys exhausted")

    with pytest.raises(QuotaExhaustedError):
        await update_stats_task.fn([mock_tracker_target], mock_strategy)
