"""
Tests for Maia Tracker module.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.tracker.flow import fetch_targets_task, update_stats_task


@pytest.fixture
def mock_strategy() -> MagicMock:
    strategy = MagicMock()
    strategy.fetch_videos = AsyncMock()
    return strategy


@pytest.mark.asyncio
async def test_fetch_targets_empty():
    """Test fetch_targets when no videos need updates."""
    with patch("maia.tracker.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.fetch_tracker_targets = AsyncMock(return_value=[])

        result = await fetch_targets_task.fn(batch_size=50)

        assert result == []
        mock_repo.fetch_tracker_targets.assert_called_once_with(50)


@pytest.mark.asyncio
async def test_fetch_targets_with_videos(mock_tracker_target: dict[str, Any]):
    """Test fetch_targets returns videos needing updates."""
    from atlas.models import Video

    with patch("maia.tracker.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.fetch_tracker_targets = AsyncMock(return_value=[Video(**mock_tracker_target)])

        result = await fetch_targets_task.fn(batch_size=50)

        assert len(result) == 1
        assert result[0]["id"] == "dQw4w9WgXcQ"


@pytest.mark.asyncio
async def test_update_stats_empty_list(mock_strategy: MagicMock):
    """Test update_stats with empty video list."""
    result = await update_stats_task.fn([], mock_strategy)
    assert result == 0


@pytest.mark.asyncio
async def test_update_stats_success(
    mock_strategy: MagicMock, mock_youtube_stats_response: dict[str, Any]
):
    """Test update_stats successfully fetches and persists statistics."""
    mock_strategy.fetch_videos.return_value = mock_youtube_stats_response

    mock_videos = [{"id": "dQw4w9WgXcQ", "title": "Test Video"}]

    with patch("maia.tracker.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.log_stats_batch = AsyncMock()
        mock_repo.update_stats_batch = AsyncMock()

        result = await update_stats_task.fn(mock_videos, mock_strategy)

        assert result == 1
        mock_repo.log_stats_batch.assert_called_once()
        mock_repo.update_stats_batch.assert_called_once()


@pytest.mark.asyncio
async def test_update_stats_handles_api_errors(
    mock_strategy: MagicMock, mock_tracker_target: dict[str, Any]
):
    """Test update_stats handles API errors gracefully."""
    mock_strategy.fetch_videos.side_effect = Exception("API Error")

    with patch("maia.tracker.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.log_stats_batch = AsyncMock()
        mock_repo.update_stats_batch = AsyncMock()

        result = await update_stats_task.fn([mock_tracker_target], mock_strategy)

        assert result == 0


@pytest.mark.asyncio
async def test_update_stats_propagates_rate_limit(
    mock_strategy: MagicMock, mock_tracker_target: dict[str, Any]
):
    """Test update_stats propagates RateLimitError."""
    from maia.utils import RateLimitError

    mock_strategy.fetch_videos.side_effect = RateLimitError("429 Rate Limit")

    with pytest.raises(RateLimitError):
        await update_stats_task.fn([mock_tracker_target], mock_strategy)
