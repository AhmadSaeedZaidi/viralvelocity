"""
Tests for Maia Hunter module.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.hunter.flow import fetch_batch_task, ingest_results_task


@pytest.fixture
def mock_strategy() -> MagicMock:
    strategy = MagicMock()
    strategy.search = AsyncMock()
    return strategy


@pytest.mark.asyncio
async def test_fetch_batch_empty_queue():
    """Test fetch_batch when queue is empty."""
    with patch("maia.hunter.flow.SearchQueueRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.fetch_batch = AsyncMock(return_value=[])

        result = await fetch_batch_task.fn(batch_size=10)

        assert result == []
        mock_repo.fetch_batch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_fetch_batch_with_items(mock_search_queue_item: dict[str, Any]):
    """Test fetch_batch with items in queue."""
    from atlas.models import SearchQueueItem

    queue_item = SearchQueueItem(**mock_search_queue_item)

    with patch("maia.hunter.flow.SearchQueueRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.fetch_batch = AsyncMock(return_value=[queue_item])

        result = await fetch_batch_task.fn(batch_size=10)

        assert len(result) == 1
        assert result[0]["query_term"] == "artificial intelligence"


@pytest.mark.asyncio
async def test_ingest_results_with_snowball(
    mock_strategy: MagicMock,
    mock_search_queue_item: dict[str, Any],
    mock_youtube_search_response: dict[str, Any],
):
    """Test ingest_results implements Snowball effect."""

    async def _passthrough_gate(items, *args, **kwargs):
        return items

    with (
        patch("maia.hunter.flow.VideoRepository") as MockVideoRepo,
        patch("maia.hunter.flow.SearchQueueRepository") as MockSearchRepo,
        patch("maia.hunter.flow.get_vault") as mock_get_vault,
        patch(
            "maia.hunter.flow.filter_by_quality",
            new=AsyncMock(side_effect=_passthrough_gate),
        ),
    ):
        mock_vault = mock_get_vault.return_value

        mock_video = MockVideoRepo.return_value
        mock_search = MockSearchRepo.return_value
        mock_video.ingest_video_metadata = AsyncMock()
        mock_search.add_terms = AsyncMock(return_value=3)
        mock_search.update_state = AsyncMock()
        mock_vault.store_metadata = MagicMock()

        await ingest_results_task.fn(
            mock_search_queue_item, mock_youtube_search_response, mock_strategy
        )

        assert mock_video.ingest_video_metadata.call_count == 1

        mock_search.add_terms.assert_called_once()
        args = mock_search.add_terms.call_args[0][0]
        assert "test" in args
        assert "example" in args
        assert "ai" in args

        mock_search.update_state.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_results_handles_vault_failure(
    mock_strategy: MagicMock,
    mock_search_queue_item: dict[str, Any],
    mock_youtube_search_response: dict[str, Any],
):
    """Test ingest_results continues even if vault storage fails."""

    async def _passthrough_gate(items, *args, **kwargs):
        return items

    with (
        patch("maia.hunter.flow.VideoRepository") as MockVideoRepo,
        patch("maia.hunter.flow.SearchQueueRepository") as MockSearchRepo,
        patch("maia.hunter.flow.get_vault") as mock_get_vault,
        patch(
            "maia.hunter.flow.filter_by_quality",
            new=AsyncMock(side_effect=_passthrough_gate),
        ),
    ):
        mock_vault = mock_get_vault.return_value

        mock_video = MockVideoRepo.return_value
        mock_search = MockSearchRepo.return_value
        mock_video.ingest_video_metadata = AsyncMock()
        mock_search.add_terms = AsyncMock(return_value=3)
        mock_search.update_state = AsyncMock()
        mock_vault.store_metadata = MagicMock(side_effect=Exception("Vault error"))

        await ingest_results_task.fn(
            mock_search_queue_item, mock_youtube_search_response, mock_strategy
        )

        assert mock_video.ingest_video_metadata.call_count == 1
