"""
Tests for Maia Tracker module.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from maia.tracker.flow import fetch_targets, update_stats


@pytest.mark.asyncio
async def test_fetch_targets_empty():
    """Test fetch_targets when no videos need updates."""
    with patch("maia.tracker.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[])

        result = await fetch_targets(batch_size=50)

        assert result == []
        mock_dao.fetch_tracker_targets.assert_called_once_with(50)


@pytest.mark.asyncio
async def test_fetch_targets_with_videos(mock_tracker_target: Dict[str, Any]):
    """Test fetch_targets returns videos needing updates."""
    with patch("maia.tracker.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[mock_tracker_target])

        result = await fetch_targets(batch_size=50)

        assert len(result) == 1
        assert result[0]["id"] == "dQw4w9WgXcQ"


@pytest.mark.asyncio
async def test_update_stats_empty_list():
    """Test update_stats with empty video list."""
    result = await update_stats([])
    assert result == 0


@pytest.mark.asyncio
async def test_update_stats_handles_api_errors(mock_tracker_target: Dict[str, Any]):
    """Test update_stats handles API errors gracefully."""
    with (
        patch("maia.tracker.flow.MaiaDAO") as MockDAO,
        patch("maia.tracker.aiohttp.ClientSession") as MockSession,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.update_video_stats_batch = AsyncMock()

        # Mock failed API response
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MockSession.return_value.__aenter__.return_value
        mock_session.get.return_value.__aenter__.return_value = mock_response

        result = await update_stats([mock_tracker_target])

        # Should return 0 (no updates) but not raise
        assert result == 0
