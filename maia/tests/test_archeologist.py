"""
Tests for Maia Archeologist module.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maia.archeologist.flow import ArcheologistAgent, archeology_flow, hunt_history_task


@pytest.mark.asyncio
async def test_hunt_history_successful_retrieval():
    """Test hunt_history successfully retrieves and ingests historical videos."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()

        mock_keys = MagicMock()
        mock_keys.next_key = MagicMock(return_value="fake_key_123")
        mock_keys.size = 3

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

        await hunt_history_task.fn(year=2010, month=5, keys=mock_keys)

        assert mock_dao.ingest_video_metadata.call_count == 10

        first_call_kwargs = mock_dao.ingest_video_metadata.call_args_list[0][1]
        assert first_call_kwargs["priority_override"] == 100


@pytest.mark.asyncio
async def test_hunt_history_handles_403_key_rotation():
    """Test Archeologist rotates keys on 403 Forbidden errors."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()

        mock_keys = MagicMock()
        mock_keys.next_key = MagicMock(side_effect=["key1", "key2", "key3"])
        mock_keys.size = 3

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        call_count = {"count": 0}

        async def mock_get_context_enter():
            call_count["count"] += 1
            if call_count["count"] <= 2:
                mock_response_403 = AsyncMock()
                mock_response_403.status = 403
                return mock_response_403
            else:
                mock_response_200 = AsyncMock()
                mock_response_200.status = 200
                mock_response_200.json = AsyncMock(return_value={"items": []})
                return mock_response_200

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = mock_get_context_enter
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        await hunt_history_task.fn(year=2010, month=1, keys=mock_keys)

        assert call_count["count"] > 2


@pytest.mark.asyncio
async def test_hunt_history_handles_429_resiliency_strategy():
    """Test Archeologist raises SystemExit on 429 rate limit (Resiliency Strategy)."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value

        mock_keys = MagicMock()
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_response = AsyncMock()
        mock_response.status = 429

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        with pytest.raises(SystemExit):
            await hunt_history_task.fn(year=2010, month=1, keys=mock_keys)


@pytest.mark.asyncio
async def test_hunt_history_handles_network_errors():
    """Test Archeologist handles network errors gracefully."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value

        mock_keys = MagicMock()
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get = MagicMock(side_effect=ConnectionError("Network down"))
        MockSession.return_value = mock_session_instance

        await hunt_history_task.fn(year=2010, month=1, keys=mock_keys)

        assert True


@pytest.mark.asyncio
async def test_run_archeology_campaign_iterates_through_years():
    """Test archeology campaign iterates through multiple years and months."""
    with patch("maia.archeologist.flow.hunt_history_task") as mock_hunt_task:
        mock_hunt_task.fn = AsyncMock()

        mock_keys = MagicMock()
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

        result = await archeology_flow.fn(start_year=2010, end_year=2011, keys=mock_keys)

        assert mock_hunt_task.fn.call_count == 24

        first_call = mock_hunt_task.fn.call_args_list[0]
        assert first_call[0] == (2010, 1, mock_keys)

        last_call = mock_hunt_task.fn.call_args_list[-1]
        assert last_call[0] == (2011, 12, mock_keys)

        assert result["years_processed"] == 2
        assert result["months_processed"] == 24


@pytest.mark.asyncio
async def test_hunt_history_handles_empty_response():
    """Test hunt_history handles empty API responses gracefully."""
    with (
        patch("maia.archeologist.flow.MaiaDAO") as MockDAO,
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()

        mock_keys = MagicMock()
        mock_keys.next_key = MagicMock(return_value="fake_key")
        mock_keys.size = 1

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

        await hunt_history_task.fn(year=2025, month=1, keys=mock_keys)

        assert mock_dao.ingest_video_metadata.call_count == 0
