"""
Tests for Maia Hunter module.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from maia.hunter import fetch_batch, ingest_results


@pytest.mark.asyncio
async def test_fetch_batch_empty_queue():
    """Test fetch_batch when queue is empty."""
    with patch("maia.hunter.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(return_value=[])
        
        result = await fetch_batch(batch_size=10)
        
        assert result == []
        mock_dao.fetch_hunter_batch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_fetch_batch_with_items(mock_search_queue_item: Dict[str, Any]):
    """Test fetch_batch with items in queue."""
    with patch("maia.hunter.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(return_value=[mock_search_queue_item])
        
        result = await fetch_batch(batch_size=10)
        
        assert len(result) == 1
        assert result[0]["query_term"] == "artificial intelligence"


@pytest.mark.asyncio
async def test_ingest_results_with_snowball(
    mock_search_queue_item: Dict[str, Any],
    mock_youtube_search_response: Dict[str, Any]
):
    """Test ingest_results implements Snowball effect."""
    with patch("maia.hunter.MaiaDAO") as MockDAO, \
         patch("maia.hunter.vault") as mock_vault:
        
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_dao.add_to_search_queue = AsyncMock(return_value=3)
        mock_dao.update_search_state = AsyncMock()
        mock_vault.store_metadata = MagicMock()
        
        await ingest_results(mock_search_queue_item, mock_youtube_search_response)
        
        # Verify video ingestion
        assert mock_dao.ingest_video_metadata.call_count == 1
        
        # Verify Snowball effect (tags added to search queue)
        mock_dao.add_to_search_queue.assert_called_once()
        args = mock_dao.add_to_search_queue.call_args[0][0]
        assert "test" in args
        assert "example" in args
        assert "ai" in args
        
        # Verify state update
        mock_dao.update_search_state.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_results_handles_vault_failure(
    mock_search_queue_item: Dict[str, Any],
    mock_youtube_search_response: Dict[str, Any]
):
    """Test ingest_results continues even if vault storage fails."""
    with patch("maia.hunter.MaiaDAO") as MockDAO, \
         patch("maia.hunter.vault") as mock_vault:
        
        mock_dao = MockDAO.return_value
        mock_dao.ingest_video_metadata = AsyncMock()
        mock_dao.add_to_search_queue = AsyncMock(return_value=3)
        mock_dao.update_search_state = AsyncMock()
        mock_vault.store_metadata = MagicMock(side_effect=Exception("Vault error"))
        
        # Should not raise, just log warning
        await ingest_results(mock_search_queue_item, mock_youtube_search_response)
        
        # Verify ingestion still happened
        assert mock_dao.ingest_video_metadata.call_count == 1

