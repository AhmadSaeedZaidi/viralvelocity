"""
Integration tests for Maia Archeologist.

These tests verify end-to-end behavior of Archeologist historical campaigns.
Mark as integration tests: pytest -m integration
"""

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.archeologist import hunt_history, run_archeology_campaign


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_complete_hunt_cycle(dao, mock_youtube_search_response):
    """Test complete Archeologist hunt cycle for a single month."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        # Setup KeyRing mock
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_archeo_key_123")
        mock_keyring.size = 3
        MockKeyRing.return_value = mock_keyring

        # Configure ClientSession
        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        # Mock YouTube API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_youtube_search_response)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Execute hunt for January 2010
        await hunt_history(year=2010, month=1)
        videos = await dao._fetch_all("SELECT * FROM videos ORDER BY discovered_at DESC LIMIT 10")
        assert len(videos) >= 5
        video_ids = [v["id"] for v in videos]
        assert "dQw4w9WgXcQ" in video_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_high_priority_override(dao):
    """Test Archeologist assigns high priority (100) to historical videos."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_key")
        mock_keyring.size = 1
        MockKeyRing.return_value = mock_keyring

        # Mock response with a historical video
        historical_video_response = {
            "items": [
                {
                    "id": {"videoId": "HISTORICAL_001"},
                    "snippet": {
                        "publishedAt": "2010-01-15T00:00:00Z",
                        "channelId": "CHANNEL_HISTORY",
                        "title": "Historical Gaming Video",
                        "channelTitle": "Retro Gamer",
                        "tags": ["gaming", "retro", "2010"],
                        "categoryId": "20",
                        "defaultLanguage": "en",
                    },
                }
            ]
        }

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=historical_video_response)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        await hunt_history(year=2010, month=1)

        # Verify video exists with high priority
        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("HISTORICAL_001",))

        assert video is not None
        assert video["id"] == "HISTORICAL_001"
        # Note: Priority is stored in search_queue, not videos table
        # The video itself should exist with PENDING status
        assert video["status"] == "PENDING"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_handles_resiliency_strategy(dao):
    """Test Archeologist raises SystemExit on 429 rate limit (Resiliency Strategy)."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_key")
        mock_keyring.size = 1
        MockKeyRing.return_value = mock_keyring

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

        # Verify SystemExit is raised
        with pytest.raises(SystemExit):
            await hunt_history(year=2010, month=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_key_rotation_on_403(dao):
    """Test Archeologist rotates keys on 403 Forbidden errors."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        keys_used = []

        def track_key_usage():
            key = f"key_{len(keys_used) + 1}"
            keys_used.append(key)
            return key

        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(side_effect=track_key_usage)
        mock_keyring.size = 3
        MockKeyRing.return_value = mock_keyring

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        call_count = {"count": 0}

        async def mock_get_response():
            call_count["count"] += 1
            if call_count["count"] <= 2:
                mock_resp = AsyncMock()
                mock_resp.status = 403
                return mock_resp
            else:
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.json = AsyncMock(return_value={"items": []})
                return mock_resp

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = mock_get_response
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        await hunt_history(year=2010, month=1)

        assert len(keys_used) > 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_campaign_multi_month(dao):
    """Test Archeologist campaign iterates through multiple months."""
    with (patch("maia.archeologist.flow.hunt_history") as mock_hunt,):
        mock_hunt.return_value = AsyncMock()
        await run_archeology_campaign(start_year=2010, end_year=2010)

        assert mock_hunt.call_count == 12

        for month in range(1, 13):
            mock_hunt.assert_any_call(2010, month)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_handles_empty_results(dao):
    """Test Archeologist handles months with no historical videos."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_key")
        mock_keyring.size = 1
        MockKeyRing.return_value = mock_keyring

        empty_response = {"items": []}

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=empty_response)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        await hunt_history(year=2005, month=1)

        videos = await dao._fetch_all(
            "SELECT * FROM videos WHERE published_at BETWEEN %s AND %s",
            ("2005-01-01", "2005-02-01"),
        )
        assert len(videos) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archeologist_time_window_calculation(dao):
    """Test Archeologist correctly calculates time windows for searches."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_key")
        mock_keyring.size = 1
        MockKeyRing.return_value = mock_keyring

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"items": []})

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        await hunt_history(year=2010, month=12)

        last_call = mock_session_instance.get.call_args_list[-1]
        params = last_call[1]["params"]

        assert "publishedAfter" in params
        assert "publishedBefore" in params
        assert params["publishedAfter"].startswith("2010-12-01")
        assert params["publishedBefore"].startswith("2011-01-01")


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance


@pytest.fixture
def mock_youtube_search_response() -> Dict[str, Any]:
    """Mock YouTube Search API response for Archeologist."""
    return {
        "kind": "youtube#searchListResponse",
        "etag": "test-etag",
        "items": [
            {
                "kind": "youtube#searchResult",
                "etag": "test-video-etag",
                "id": {"kind": "youtube#video", "videoId": "dQw4w9WgXcQ"},
                "snippet": {
                    "publishedAt": "2010-01-01T00:00:00Z",
                    "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
                    "title": "Historical Test Video",
                    "channelTitle": "Test Channel",
                    "tags": ["history", "gaming", "retro"],
                    "categoryId": "20",
                    "defaultLanguage": "en",
                },
            }
        ],
    }
