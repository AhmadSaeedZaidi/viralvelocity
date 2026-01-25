"""
Integration tests for Maia flows.

These tests verify end-to-end behavior of Hunter and Tracker cycles.
Mark as integration tests: pytest -m integration
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.hunter import run_hunter_cycle
from maia.tracker import run_tracker_cycle


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_cycle_complete_flow(
    mock_search_queue_item: Dict[str, Any], mock_youtube_search_response: Dict[str, Any]
):
    """Test complete Hunter cycle from fetch to ingest."""
    with (
        patch("maia.hunter.flow.MaiaDAO") as MockDAO,
        patch("maia.hunter.flow.vault") as mock_vault,
        patch("maia.hunter.flow.aiohttp.ClientSession") as MockSession,
    ):

        # Setup mocks
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(return_value=[mock_search_queue_item])
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_dao.add_to_search_queue = AsyncMock(return_value=3)
        mock_dao.update_search_state = AsyncMock()
        mock_vault.store_metadata = MagicMock()

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

        # Execute cycle
        stats = await run_hunter_cycle(batch_size=1)

        # Assertions
        assert stats["queries_processed"] == 1
        assert stats["videos_discovered"] == 1
        assert stats["searches_successful"] == 1
        assert stats["searches_failed"] == 0

        # Verify DAO calls
        mock_dao.fetch_hunter_batch.assert_called_once_with(1)
        mock_dao.ingest_video_metadata.assert_called_once()
        mock_dao.add_to_search_queue.assert_called_once()
        mock_dao.update_search_state.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tracker_cycle_complete_flow(
    mock_tracker_target: Dict[str, Any], mock_youtube_stats_response: Dict[str, Any]
):
    """Test complete Tracker cycle from fetch to update."""
    with (
        patch("maia.tracker.flow.MaiaDAO") as MockDAO,
        patch("maia.tracker.flow.aiohttp.ClientSession") as MockSession,
    ):
        # Setup DAO mocks
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[mock_tracker_target])
        mock_dao.update_video_stats_batch = AsyncMock()

        # --- FIX: Configure ClientSession as Async Context Manager
        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        # Configure session.get() as Async Context Manager
        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock()  # Will set return_value below
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance
        # ----------------------------------------------------

        # Mock YouTube API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_youtube_stats_response)
        mock_get_context.__aenter__.return_value = mock_response

        # Execute cycle
        stats = await run_tracker_cycle(batch_size=1)
        # Assertions
        assert stats["videos_fetched"] == 1
        assert stats["videos_updated"] == 1
        assert stats["updates_failed"] == 0

        mock_dao.fetch_tracker_targets.assert_called_once_with(1)
        mock_dao.update_video_stats_batch.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_handles_hydra_protocol():
    """Test Hunter raises SystemExit on 429 rate limit (Hydra Protocol)."""
    with (
        patch("maia.hunter.flow.MaiaDAO") as MockDAO,
        patch("maia.hunter.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(
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

        with pytest.raises(SystemExit, match="429 Rate Limit"):
            await run_hunter_cycle(batch_size=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tracker_handles_hydra_protocol():
    """Test Tracker raises SystemExit on 429 rate limit (Hydra Protocol)."""
    with (
        patch("maia.tracker.flow.MaiaDAO") as MockDAO,
        patch("maia.tracker.flow.aiohttp.ClientSession") as MockSession,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(
            return_value=[
                {
                    "id": "test123",
                    "title": "Test Video",
                    "published_at": "2023-01-01T00:00:00Z",
                    "last_updated_at": None,
                }
            ]
        )

        # --- FIX START ---
        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_get_context = MagicMock()
        mock_get_context.__aenter__ = AsyncMock()
        mock_get_context.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance.get.return_value = mock_get_context
        MockSession.return_value = mock_session_instance
        # --- FIX END ---

        # Mock 429 response
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_get_context.__aenter__.return_value = mock_response

        with pytest.raises(SystemExit, match="429 Rate Limit"):
            await run_tracker_cycle(batch_size=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_empty_queue_returns_idle():
    """Test Hunter handles empty queue gracefully."""
    with patch("maia.hunter.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(return_value=[])

        stats = await run_hunter_cycle(batch_size=10)

        assert stats["queries_processed"] == 0
        assert stats["videos_discovered"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tracker_no_stale_videos_returns_idle():
    """Test Tracker handles no stale videos gracefully."""
    with patch("maia.tracker.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[])

        stats = await run_tracker_cycle(batch_size=50)

        assert stats["videos_fetched"] == 0
        assert stats["videos_updated"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tracker_enforces_batch_size_limit():
    """Test Tracker enforces YouTube API batch size limit of 50."""
    with patch("maia.tracker.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_tracker_targets = AsyncMock(return_value=[])

        # Request 100 but should cap at 50
        stats = await run_tracker_cycle(batch_size=100)

        # Verify batch_size was capped at 50
        mock_dao.fetch_tracker_targets.assert_called_once_with(50)
