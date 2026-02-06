"""
Tests for Maia Archeologist module.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maia.archeologist import hunt_history, run_archeology_campaign


@pytest.mark.asyncio
async def test_hunt_history_successful_retrieval():
    """Test hunt_history successfully retrieves and ingests historical videos."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.archeo_keys") as mock_keys,
    ):
        # Setup mocks
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_keys.next_key = MagicMock(return_value="fake_key_123")
        mock_keys.size = 3

        # Mock API response with historical videos
        mock_response_data = {
            "kind": "youtube#searchListResponse",
            "items": [
                {
                    "id": {"videoId": "OLD_VIDEO_001"},
                    "snippet": {
                        "publishedAt": "2010-05-15T00:00:00Z",
                        "channelId": "CHANNEL_HISTORY_001",
                        "title": "Historical Video",
                        "channelTitle": "Historical Channel",
                        "tags": ["history", "gaming"],
                        "categoryId": "20",
                        "defaultLanguage": "en",
                    },
                },
                {
                    "id": {"videoId": "OLD_VIDEO_002"},
                    "snippet": {
                        "publishedAt": "2010-05-20T00:00:00Z",
                        "channelId": "CHANNEL_HISTORY_002",
                        "title": "Another Old Video",
                        "channelTitle": "Vintage Channel",
                        "tags": ["retro"],
                        "categoryId": "20",
                        "defaultLanguage": "en",
                    },
                },
            ],
        }

        # Configure ClientSession
        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_response_data)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Execute
        await hunt_history(year=2010, month=5)

        # Verify ingestion happened for all items in all categories
        # TARGET_CATEGORIES = ["10", "20", "24", "28", "27"] (5 categories)
        # Each category gets 2 items from mock response
        # Total calls = 5 categories × 2 items = 10
        assert mock_dao.ingest_video_metadata.call_count == 10

        # Verify priority override was used
        first_call_kwargs = mock_dao.ingest_video_metadata.call_args_list[0][1]
        assert first_call_kwargs["priority_override"] == 100


@pytest.mark.asyncio
async def test_hunt_history_handles_403_key_rotation():
    """Test Archeologist rotates keys on 403 Forbidden errors."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.archeo_keys") as mock_keys,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()

        # Simulate 3 keys available
        mock_keys.next_key = MagicMock(side_effect=["key1", "key2", "key3"])
        mock_keys.size = 3

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        # First two calls return 403, third succeeds
        mock_response_403 = AsyncMock()
        mock_response_403.status = 403

        mock_response_200 = AsyncMock()
        mock_response_200.status = 200
        mock_response_200.json = AsyncMock(return_value={"items": []})

        call_count = {"count": 0}

        async def mock_get_context_enter():
            call_count["count"] += 1
            if call_count["count"] <= 2:
                return mock_response_403
            return mock_response_200

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = mock_get_context_enter
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Execute
        await hunt_history(year=2010, month=1)

        # Verify key rotation happened (3 attempts per category)
        # We have 5 categories, but we only check that rotation logic was triggered
        assert call_count["count"] > 2  # At least rotated once


@pytest.mark.asyncio
async def test_hunt_history_handles_429_hydra_protocol():
    """Test Archeologist raises SystemExit on 429 rate limit (Resiliency Strategy)."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.archeo_keys") as mock_keys,
    ):
        mock_dao = MockDAO.return_value
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        # Mock 429 response
        mock_response = AsyncMock()
        mock_response.status = 429

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Execute and verify SystemExit is raised
        with pytest.raises(SystemExit):
            await hunt_history(year=2010, month=1)


@pytest.mark.asyncio
async def test_hunt_history_handles_network_errors():
    """Test Archeologist handles network errors gracefully."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.archeo_keys") as mock_keys,
    ):
        mock_dao = MockDAO.return_value
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        # Mock network error
        mock_session_instance.get = MagicMock(side_effect=ConnectionError("Network down"))
        MockSession.return_value = mock_session_instance

        # Should not raise, just log error
        await hunt_history(year=2010, month=1)

        # If we get here, the error was handled gracefully
        assert True


@pytest.mark.asyncio
async def test_run_archeology_campaign_iterates_through_years():
    """Test archeology campaign iterates through multiple years and months."""
    with patch("maia.archeologist.flow.hunt_history") as mock_hunt:
        mock_hunt.return_value = AsyncMock()

        # Run campaign for 2 years
        await run_archeology_campaign(start_year=2010, end_year=2011)

        # Verify hunt_history was called for each month in the range
        # 2010: 12 months, 2011: 12 months = 24 total
        assert mock_hunt.call_count == 24

        # Verify first and last calls
        first_call = mock_hunt.call_args_list[0]
        assert first_call[0] == (2010, 1)

        last_call = mock_hunt.call_args_list[-1]
        assert last_call[0] == (2011, 12)


@pytest.mark.asyncio
async def test_hunt_history_handles_empty_response():
    """Test hunt_history handles empty API responses gracefully."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.archeo_keys") as mock_keys,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

        # Mock empty response
        mock_response_data = {"kind": "youtube#searchListResponse", "items": []}

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_response_data)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Execute
        await hunt_history(year=2025, month=1)

        # Should complete without errors, but no ingestions
        assert mock_dao.ingest_video_metadata.call_count == 0
