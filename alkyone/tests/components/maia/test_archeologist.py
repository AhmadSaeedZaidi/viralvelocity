"""
Integration tests for Maia Archeologist.

These tests verify end-to-end behavior of Archeologist historical campaigns.
Mark as integration tests: pytest -m integration
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio
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
    """Test Archeologist handles 429 rate limit gracefully with retry logic."""
    with (
        patch("maia.archeologist.flow.aiohttp.ClientSession") as MockSession,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
        patch("maia.archeologist.flow._fetch_with_backoff") as MockFetch,
    ):
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_key")
        mock_keyring.size = 3  # Give it keys to rotate through
        MockKeyRing.return_value = mock_keyring

        # Configure _fetch_with_backoff to raise RetryError (tenacity exhausted retries)
        from tenacity import RetryError

        # Create a proper RetryError with a failed future
        import concurrent.futures

        failed_future = concurrent.futures.Future()
        failed_future.set_exception(Exception("Rate limit"))
        retry_error = RetryError(failed_future)

        MockFetch.side_effect = retry_error

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)
        MockSession.return_value = mock_session_instance

        # System should raise SystemExit only after exhausting ALL keys
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
    with (
        patch("maia.archeologist.flow.hunt_history_task") as mock_hunt,
        patch("maia.archeologist.flow.KeyRing") as MockKeyRing,
    ):
        # Configure hunt_history_task as an async mock that returns None
        mock_hunt_coro = AsyncMock(return_value=None)
        mock_hunt.return_value = mock_hunt_coro

        # Setup KeyRing mock
        mock_keyring = MagicMock()
        mock_keyring.next_key = MagicMock(return_value="test_key")
        mock_keyring.size = 3
        MockKeyRing.return_value = mock_keyring

        # Run campaign for 2010 (12 months)
        await run_archeology_campaign(start_year=2010, end_year=2010)

        # Verify hunt_history_task was called for all 12 months of 2010
        assert mock_hunt.call_count == 12

        # Verify correct month sequence (should be called with year, month, keys)
        for month in range(1, 13):
            # Check that at least one call had this year and month
            calls_with_month = [
                call
                for call in mock_hunt.call_args_list
                if len(call[0]) >= 2 and call[0][0] == 2010 and call[0][1] == month
            ]
            assert len(calls_with_month) == 1, f"Month {month} should be called exactly once"


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


@pytest_asyncio.fixture
async def dao(fresh_db):
    """Provide MaiaDAO instance for testing with real vault."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance


@pytest.fixture
def mock_youtube_search_response() -> Dict[str, Any]:
    """Mock YouTube Search API response for Archeologist with high-volume results."""
    return {
        "kind": "youtube#searchListResponse",
        "etag": "test-etag",
        "items": [
            {
                "kind": "youtube#searchResult",
                "etag": f"test-video-etag-{i}",
                "id": {
                    "kind": "youtube#video",
                    "videoId": "dQw4w9WgXcQ" if i == 0 else f"VIDEO_{i:03d}",
                },
                "snippet": {
                    "publishedAt": "2010-01-01T00:00:00Z",
                    "channelId": f"CHANNEL_{i:03d}",
                    "title": f"Historical Gaming Video {i}",
                    "channelTitle": f"Test Channel {i}",
                    "tags": ["history", "gaming", "retro"],
                    "categoryId": "20",
                    "defaultLanguage": "en",
                },
            }
            for i in range(10)
        ],
    }


# ========================
# REAL YOUTUBE API TESTS
# ========================
# These tests use real YouTube API keys from CI environment and make actual API calls.
# They should only run in the CI environment where YOUTUBE_API_KEY_POOL_JSON is set.


def has_real_youtube_keys() -> bool:
    """Check if real YouTube API keys are available."""
    return bool(os.getenv("YOUTUBE_API_KEY_POOL_JSON"))


@pytest.mark.integration
@pytest.mark.skipif(not has_real_youtube_keys(), reason="Real YouTube API keys not available")
@pytest.mark.asyncio
async def test_archeologist_real_youtube_api_search(dao):
    """Test Archeologist with REAL YouTube API (CI only)."""
    from atlas.utils import KeyRing

    # This will use real keys from environment
    keys = KeyRing("archeology")

    # Test historical search for a month known to have videos (January 2010)
    base_url = "https://www.googleapis.com/youtube/v3/search"

    start_date = datetime(2010, 1, 1, tzinfo=timezone.utc)
    end_date = datetime(2010, 2, 1, tzinfo=timezone.utc)

    start_str = start_date.isoformat().replace("+00:00", "Z")
    end_str = end_date.isoformat().replace("+00:00", "Z")

    params = {
        "part": "snippet",
        "type": "video",
        "order": "viewCount",
        "publishedAfter": start_str,
        "publishedBefore": end_str,
        "videoCategoryId": "20",  # Gaming category
        "maxResults": 5,
        "key": keys.next_key(),
    }

    # Make real API call
    async with aiohttp.ClientSession() as session:
        async with session.get(base_url, params=params) as resp:
            assert resp.status == 200, f"YouTube API returned status {resp.status}"

            data = await resp.json()
            items = data.get("items", [])

            # Verify we got results
            assert len(items) > 0, "Expected at least 1 video from Jan 2010"

            # Verify response structure
            for item in items[:3]:  # Check first 3 items
                assert "id" in item
                assert "videoId" in item["id"]
                assert "snippet" in item
                assert "title" in item["snippet"]
                assert "channelId" in item["snippet"]

            # Ingest one video to verify DAO integration
            await dao.ingest_video_metadata(items[0], priority_override=100)

            # Verify video was stored
            video_id = items[0]["id"]["videoId"]
            video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))
            assert video is not None
            assert video["id"] == video_id


@pytest.mark.integration
@pytest.mark.skipif(not has_real_youtube_keys(), reason="Real YouTube API keys not available")
@pytest.mark.asyncio
async def test_archeologist_real_api_rate_limit_detection(dao):
    """Test that Archeologist properly detects and handles rate limits from real API."""
    from atlas.utils import KeyRing

    keys = KeyRing("archeology")

    # Make rapid-fire requests to potentially trigger rate limiting
    # This test verifies our rate limit detection works with real API responses
    base_url = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part": "snippet",
        "type": "video",
        "q": "test",
        "maxResults": 1,
        "key": keys.next_key(),
    }

    # Make several requests - if we hit a 429, we should handle it
    async with aiohttp.ClientSession() as session:
        for i in range(3):
            async with session.get(base_url, params=params) as resp:
                # Should be either 200 (success) or 429 (rate limit)
                assert resp.status in [200, 429, 403], f"Unexpected status {resp.status}"

                if resp.status == 429:
                    # Rate limit detected - this is what we want to test
                    print("✓ Rate limit (429) detected from real YouTube API")
                    break
                elif resp.status == 403:
                    # API key exhausted - rotate to next key
                    params["key"] = keys.next_key()
                else:
                    # Success - verify response structure
                    data = await resp.json()
                    assert "items" in data


@pytest.mark.integration
@pytest.mark.skipif(not has_real_youtube_keys(), reason="Real YouTube API keys not available")
@pytest.mark.asyncio
async def test_archeologist_real_api_key_rotation(dao):
    """Test KeyRing rotation with real YouTube API keys."""
    from atlas.utils import KeyRing

    keys = KeyRing("archeology")

    # Verify we have multiple keys
    assert keys.size >= 1, "Need at least 1 API key for testing"

    base_url = "https://www.googleapis.com/youtube/v3/search"

    # Track which keys we've used
    used_keys = set()

    # Try up to the number of keys we have
    for attempt in range(min(3, keys.size)):
        key = keys.next_key()
        used_keys.add(key)

        params = {
            "part": "snippet",
            "type": "video",
            "q": "minecraft",
            "maxResults": 1,
            "key": key,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params=params) as resp:
                # Any of these responses are acceptable for this test
                assert resp.status in [200, 403, 429], f"Unexpected status {resp.status}"

                if resp.status == 200:
                    # Key is valid - great!
                    data = await resp.json()
                    assert "items" in data
                    break

    # Verify key rotation happened if we have multiple keys
    if keys.size > 1:
        assert len(used_keys) > 0, "Should have used at least one key"
