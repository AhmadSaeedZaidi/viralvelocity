"""
Validation tests for Maia components.

Tests for input validation, error handling, and edge cases.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maia.hunter import ingest_results
from maia.tracker import update_stats


@pytest.mark.asyncio
async def test_ingest_results_handles_missing_video_id():
    """Test ingest_results gracefully handles missing video ID."""
    topic = {"id": 1, "query_term": "test"}
    response = {
        "items": [
            {
                "id": {},  # Missing videoId
                "snippet": {
                    "channelId": "UC123",
                    "channelTitle": "Test",
                    "tags": ["test"],
                },
            }
        ]
    }

    with (
        patch("maia.hunter.MaiaDAO") as MockDAO,
        patch("maia.hunter.vault") as mock_vault,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_dao.add_to_search_queue = AsyncMock(return_value=1)
        mock_dao.update_search_state = AsyncMock()
        mock_vault.store_metadata = MagicMock()

        # Should not raise
        await ingest_results(topic, response)

        # Should still process tags even if video ingestion fails
        mock_dao.add_to_search_queue.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_results_handles_empty_tags():
    """Test ingest_results handles empty/invalid tags properly."""
    topic = {"id": 1, "query_term": "test"}
    response = {
        "items": [
            {
                "id": {"videoId": "test123"},
                "snippet": {
                    "channelId": "UC123",
                    "channelTitle": "Test",
                    "tags": ["", "  ", None, "valid_tag", ""],  # Mixed valid/invalid
                },
            }
        ]
    }

    with (
        patch("maia.hunter.MaiaDAO") as MockDAO,
        patch("maia.hunter.vault") as mock_vault,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_dao.add_to_search_queue = AsyncMock(return_value=1)
        mock_dao.update_search_state = AsyncMock()
        mock_vault.store_metadata = MagicMock()

        await ingest_results(topic, response)

        # Verify only valid tag was added
        args = mock_dao.add_to_search_queue.call_args[0][0]
        assert "valid_tag" in args
        assert len(args) == 1  # Only one valid tag


@pytest.mark.asyncio
async def test_ingest_results_handles_missing_tags():
    """Test ingest_results handles videos without tags field."""
    topic = {"id": 1, "query_term": "test"}
    response = {
        "items": [
            {
                "id": {"videoId": "test123"},
                "snippet": {
                    "channelId": "UC123",
                    "channelTitle": "Test",
                    # No tags field
                },
            }
        ]
    }

    with (
        patch("maia.hunter.MaiaDAO") as MockDAO,
        patch("maia.hunter.vault") as mock_vault,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_dao.add_to_search_queue = AsyncMock(return_value=0)
        mock_dao.update_search_state = AsyncMock()
        mock_vault.store_metadata = MagicMock()

        # Should not raise
        await ingest_results(topic, response)

        # No tags to add but should still work
        mock_dao.ingest_video_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_results_handles_none_response():
    """Test ingest_results returns early on None response."""
    topic = {"id": 1, "query_term": "test"}

    with patch("maia.hunter.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()

        # Should return early without calling DAO
        await ingest_results(topic, None)

        mock_dao.ingest_video_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_update_stats_handles_deleted_videos():
    """Test update_stats handles videos that were deleted/made private."""
    videos = [{"id": "deleted123", "title": "Deleted Video"}]

    with (
        patch("maia.tracker.MaiaDAO") as MockDAO,
        patch("maia.tracker.aiohttp.ClientSession") as MockSession,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.update_video_stats_batch = AsyncMock()

        # API returns empty items (video deleted/private)
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"items": []})

        mock_session = MockSession.return_value.__aenter__.return_value
        mock_session.get.return_value.__aenter__.return_value = mock_response

        result = await update_stats(videos)

        # Should return 0 updates (no videos found)
        assert result == 0
        mock_dao.update_video_stats_batch.assert_not_called()


@pytest.mark.asyncio
async def test_update_stats_handles_network_errors():
    """Test update_stats handles network errors gracefully."""
    videos = [{"id": "test123", "title": "Test Video"}]

    with (
        patch("maia.tracker.MaiaDAO") as MockDAO,
        patch("maia.tracker.aiohttp.ClientSession") as MockSession,
    ):

        mock_dao = MockDAO.return_value

        # Simulate network error
        mock_session = MockSession.return_value.__aenter__.return_value
        mock_session.get.side_effect = Exception("Connection refused")

        result = await update_stats(videos)

        # Should return 0 updates (graceful failure)
        assert result == 0


@pytest.mark.asyncio
async def test_update_stats_partial_success():
    """Test update_stats handles partial failures in batch."""
    videos = [
        {"id": "valid1", "title": "Video 1"},
        {"id": "valid2", "title": "Video 2"},
    ]

    with (
        patch("maia.tracker.MaiaDAO") as MockDAO,
        patch("maia.tracker.aiohttp.ClientSession") as MockSession,
    ):

        mock_dao = MockDAO.return_value
        mock_dao.update_video_stats_batch = AsyncMock()

        # API returns only one video (other deleted/private)
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "items": [
                    {
                        "id": "valid1",
                        "statistics": {"viewCount": "1000", "likeCount": "50"},
                    }
                ]
            }
        )

        mock_session = MockSession.return_value.__aenter__.return_value
        mock_session.get.return_value.__aenter__.return_value = mock_response

        result = await update_stats(videos)

        # Should return 1 (partial success)
        assert result == 1
        mock_dao.update_video_stats_batch.assert_called_once()
