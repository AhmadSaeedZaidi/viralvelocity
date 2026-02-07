"""
Tests for Maia Tracker module.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maia.tracker.flow import fetch_targets_task, update_stats_task


@pytest.mark.asyncio
async def test_fetch_targets_empty():
    """Test fetch_targets when no videos need updates."""
    with patch("maia.tracker.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[])

        result = await fetch_targets_task.fn(batch_size=50)

        assert result == []
        mock_dao.fetch_tracker_targets.assert_called_once_with(50)


@pytest.mark.asyncio
async def test_fetch_targets_with_videos(mock_tracker_target: Dict[str, Any]):
    """Test fetch_targets returns videos needing updates."""
    with patch("maia.tracker.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[mock_tracker_target])

        result = await fetch_targets_task.fn(batch_size=50)

        assert len(result) == 1
        assert result[0]["id"] == "dQw4w9WgXcQ"


@pytest.mark.asyncio
async def test_update_stats_empty_list():
    """Test update_stats with empty video list."""
    mock_executor = MagicMock()
    result = await update_stats_task.fn([], mock_executor)
    assert result == 0


@pytest.mark.asyncio
async def test_update_stats_handles_api_errors(mock_tracker_target: Dict[str, Any]):
    """Test update_stats handles API errors gracefully."""
    with (
        patch("maia.tracker.flow.MaiaDAO") as MockDAO,
        patch("maia.tracker.flow.aiohttp.ClientSession") as MockSession,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.update_video_stats_batch = AsyncMock()

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MockSession.return_value.__aenter__.return_value
        mock_session.get.return_value.__aenter__.return_value = mock_response

        mock_executor = MagicMock()
        mock_executor.execute_async = AsyncMock(side_effect=Exception("API Error"))

        result = await update_stats_task.fn([mock_tracker_target], mock_executor)

        assert result == 0
