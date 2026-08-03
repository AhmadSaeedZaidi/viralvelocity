"""
Unit tests for Maia flows.

These tests verify Hunter and Tracker cycle logic using fully-mocked
DAO and HTTP layers — no real infrastructure required.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from atlas.utils import QuotaExhaustedError
from maia.hunter import run_hunter_cycle
from maia.tracker import run_tracker_cycle


@pytest.mark.asyncio
async def test_hunter_cycle_complete_flow(
    mock_search_queue_item: dict[str, Any], mock_youtube_search_response: dict[str, Any]
):
    """Test complete Hunter cycle from fetch to ingest with real vault storage."""
    with (
        patch("maia.hunter.flow.SearchQueueRepository") as MockSearchRepo,
        patch("maia.hunter.flow.VideoRepository") as MockVideoRepo,
        patch("maia.hunter.flow.aiohttp.ClientSession") as MockSession,
    ):
        # Setup mocks
        mock_search_repo = MockSearchRepo.return_value
        mock_video_repo = MockVideoRepo.return_value
        mock_search_repo.fetch_batch = AsyncMock(return_value=[mock_search_queue_item])
        mock_video_repo.ingest_video_metadata = AsyncMock()
        mock_search_repo.add_terms = AsyncMock(return_value=3)
        mock_search_repo.update_state = AsyncMock()

        # Configure ClientSession to be an Async Context Manager
        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        # Mock YouTube API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_youtube_search_response)

        # Configure session.get() to be an Async Context Manager
        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Execute cycle with real vault
        stats = await run_hunter_cycle(batch_size=1)

        # Assertions
        assert stats["queries_processed"] == 1, f"Expected 1 query processed, got {stats}"
        assert stats["videos_discovered"] == 1, f"Expected 1 video discovered, got {stats}"
        assert stats["searches_successful"] == 1, f"Expected 1 successful search, got {stats}"
        assert stats["searches_failed"] == 0, f"Expected 0 failed searches, got {stats}"

        # Verify DAO calls
        mock_search_repo.fetch_batch.assert_called_once_with(1)
        mock_video_repo.ingest_video_metadata.assert_called_once()
        mock_search_repo.add_terms.assert_called_once()
        mock_search_repo.update_state.assert_called_once()


@pytest.mark.asyncio
async def test_tracker_cycle_complete_flow(
    mock_tracker_target: dict[str, Any], mock_youtube_stats_response: dict[str, Any]
):
    """Test complete Tracker cycle from fetch to update."""
    with (
        patch("maia.tracker.flow.VideoRepository") as MockVideoRepo,
        patch("maia.tracker.flow.aiohttp.ClientSession") as MockSession,
    ):
        # Setup DAO mocks
        mock_video_repo = MockVideoRepo.return_value
        from atlas.models import Video

        mock_video_repo.fetch_tracker_targets = AsyncMock(
            return_value=[Video(**mock_tracker_target)]
        )
        mock_video_repo.update_stats_batch = AsyncMock()
        mock_video_repo.log_stats_batch = AsyncMock()

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock()
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_youtube_stats_response)
        mock_get_context.__aenter__.return_value = mock_response

        stats = await run_tracker_cycle(batch_size=1)
        assert stats["videos_fetched"] == 1, f"Expected 1 video fetched, got {stats}"
        assert stats["videos_updated"] == 1, f"Expected 1 video updated, got {stats}"
        assert stats["updates_failed"] == 0, f"Expected 0 failed updates, got {stats}"

        mock_video_repo.fetch_tracker_targets.assert_called_once_with(1)
        mock_video_repo.update_stats_batch.assert_called_once()


@pytest.mark.asyncio
async def test_hunter_handles_resiliency_strategy():
    """Test Hunter raises QuotaExhaustedError on all-keys-exhausted."""
    with (
        patch("maia.hunter.flow.SearchQueueRepository") as MockSearchRepo,
        patch("maia.hunter.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_search_repo = MockSearchRepo.return_value
        mock_search_repo.fetch_batch = AsyncMock(
            return_value=[
                {
                    "id": 1,
                    "query_term": "test",
                    "next_page_token": None,
                    "last_searched_at": None,
                    "priority": 5,
                }
            ]
        )

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock()
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        # Mock 429 response
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_get_context.__aenter__.return_value = mock_response

        with pytest.raises(QuotaExhaustedError):
            await run_hunter_cycle(batch_size=1)


@pytest.mark.asyncio
async def test_tracker_handles_resiliency_strategy():
    """Test Tracker raises QuotaExhaustedError on all-keys-exhausted."""
    with (
        patch("maia.tracker.flow.VideoRepository") as MockVideoRepo,
        patch("maia.tracker.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_video_repo = MockVideoRepo.return_value
        from atlas.models import Video

        mock_video_repo.fetch_tracker_targets = AsyncMock(
            return_value=[
                Video(
                    id="test123",
                    title="Test Video",
                    published_at="2023-01-01T00:00:00Z",
                    last_updated_at=None,
                )
            ]
        )
        mock_video_repo.log_stats_batch = AsyncMock()

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock()
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance

        mock_response = AsyncMock()
        mock_response.status = 429
        mock_get_context.__aenter__.return_value = mock_response

        with pytest.raises(QuotaExhaustedError):
            await run_tracker_cycle(batch_size=1)


@pytest.mark.asyncio
async def test_hunter_empty_queue_returns_idle():
    """Test Hunter handles empty queue gracefully."""
    with patch("maia.hunter.flow.SearchQueueRepository") as MockSearchRepo:
        mock_search_repo = MockSearchRepo.return_value
        mock_search_repo.fetch_batch = AsyncMock(return_value=[])

        stats = await run_hunter_cycle(batch_size=10)

        assert stats["queries_processed"] == 0, f"Expected idle cycle (0 queries), got {stats}"
        assert stats["videos_discovered"] == 0, f"Expected 0 videos on empty queue, got {stats}"


@pytest.mark.asyncio
async def test_tracker_no_stale_videos_returns_idle():
    """Test Tracker handles no stale videos gracefully."""
    with patch("maia.tracker.flow.WatchlistRepository") as MockWatchRepo:
        mock_watch = MockWatchRepo.return_value
        mock_watch.fetch_batch = AsyncMock(return_value=[])

        stats = await run_tracker_cycle(batch_size=50)

        assert stats["videos_fetched"] == 0, f"Expected idle cycle (0 fetched), got {stats}"
        assert stats["videos_updated"] == 0, f"Expected 0 updates on idle cycle, got {stats}"


@pytest.mark.asyncio
async def test_tracker_enforces_batch_size_limit():
    """Test Tracker enforces YouTube API batch size limit of 50."""
    with patch("maia.tracker.flow.WatchlistRepository") as MockWatchRepo:
        mock_watch = MockWatchRepo.return_value
        mock_watch.fetch_batch = AsyncMock(return_value=[])

        # Request 100 but should cap at 50
        await run_tracker_cycle(batch_size=100)

        # Verify batch_size was capped at 50
        mock_watch.fetch_batch.assert_called_once_with(50)
