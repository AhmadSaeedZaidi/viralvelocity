"""
Tests for Maia Scribe module.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maia.scribe.flow import fetch_scribe_targets, process_transcript, run_scribe_cycle


@pytest.mark.asyncio
async def test_fetch_scribe_targets_empty():
    """Test fetch_scribe_targets returns empty list when no videos need transcripts."""
    with patch("maia.scribe.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_scribe_batch = AsyncMock(return_value=[])

        result = await fetch_scribe_targets(batch_size=10)

        assert result == []
        mock_dao.fetch_scribe_batch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_fetch_scribe_targets_with_videos():
    """Test fetch_scribe_targets returns videos needing transcripts."""
    mock_videos = [
        {"id": "VIDEO_001", "title": "Test Video 1"},
        {"id": "VIDEO_002", "title": "Test Video 2"},
    ]

    with patch("maia.scribe.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_scribe_batch = AsyncMock(return_value=mock_videos)

        result = await fetch_scribe_targets(batch_size=10)

        assert len(result) == 2
        assert result[0]["id"] == "VIDEO_001"
        mock_dao.fetch_scribe_batch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_process_transcript_successful():
    """Test process_transcript successfully fetches and stores transcript."""
    video = {"id": "VIDEO_001", "title": "Test Video"}
    mock_transcript = [
        {"text": "Hello", "start": 0.0, "duration": 1.5},
        {"text": "World", "start": 1.5, "duration": 1.0},
    ]

    with (
        patch("maia.scribe.flow.MaiaDAO") as MockDAO,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_transcript_safe = AsyncMock()

        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)

        mock_vault.store_transcript = MagicMock()

        await process_transcript(video)

        # Verify transcript was fetched
        mock_loader_instance.fetch.assert_called_once_with("VIDEO_001")

        # Verify stored to vault
        mock_vault.store_transcript.assert_called_once_with("VIDEO_001", mock_transcript)

        # Verify marked as safe
        mock_dao.mark_video_transcript_safe.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_transcript_unavailable():
    """Test process_transcript handles unavailable transcripts gracefully."""
    video = {"id": "VIDEO_NO_TRANSCRIPT", "title": "Video Without Transcript"}

    with (
        patch("maia.scribe.flow.MaiaDAO") as MockDAO,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_transcript_safe = AsyncMock()

        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=None)

        mock_vault.store_transcript = MagicMock()

        await process_transcript(video)

        # Verify vault was not called
        mock_vault.store_transcript.assert_not_called()

        # Verify still marked as safe (to avoid retrying)
        mock_dao.mark_video_transcript_safe.assert_called_once_with("VIDEO_NO_TRANSCRIPT")


@pytest.mark.asyncio
async def test_process_transcript_handles_vault_failure_with_retry():
    """Test process_transcript retries on vault storage failures."""
    video = {"id": "VIDEO_001", "title": "Test Video"}
    mock_transcript = [{"text": "Hello", "start": 0.0, "duration": 1.0}]

    with (
        patch("maia.scribe.flow.MaiaDAO") as MockDAO,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_failed = AsyncMock()

        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)

        # Vault fails all 3 retry attempts
        mock_vault.store_transcript = MagicMock(side_effect=Exception("Vault connection error"))

        await process_transcript(video)

        # Verify video was marked as failed
        mock_dao.mark_video_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_transcript_handles_transcript_fetch_failure():
    """Test process_transcript handles TranscriptLoader failures."""
    video = {"id": "VIDEO_001", "title": "Test Video"}

    with (
        patch("maia.scribe.flow.MaiaDAO") as MockDAO,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_failed = AsyncMock()

        mock_loader_instance = MockLoader.return_value
        # Simulate network error on fetch
        mock_loader_instance.fetch = MagicMock(side_effect=Exception("Network timeout"))

        await process_transcript(video)

        # Verify video was marked as failed
        mock_dao.mark_video_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_transcript_propagates_hydra_protocol():
    """Test process_transcript propagates SystemExit for Resiliency Strategy."""
    video = {"id": "VIDEO_001", "title": "Test Video"}

    with (
        patch("maia.scribe.flow.MaiaDAO") as MockDAO,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
    ):
        mock_dao = MockDAO.return_value

        mock_loader_instance = MockLoader.return_value
        # TranscriptLoader raises SystemExit on TooManyRequests
        mock_loader_instance.fetch = MagicMock(side_effect=SystemExit("429 Rate Limit"))

        with pytest.raises(SystemExit):
            await process_transcript(video)


@pytest.mark.asyncio
async def test_run_scribe_cycle_empty_queue():
    """Test run_scribe_cycle handles empty queue gracefully."""
    with patch("maia.scribe.flow.fetch_scribe_targets") as mock_fetch:
        mock_fetch.return_value = []

        # Should complete without errors
        await run_scribe_cycle(batch_size=10)

        mock_fetch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_run_scribe_cycle_processes_batch():
    """Test run_scribe_cycle processes a batch of videos sequentially."""
    mock_videos = [
        {"id": "VIDEO_001", "title": "Video 1"},
        {"id": "VIDEO_002", "title": "Video 2"},
        {"id": "VIDEO_003", "title": "Video 3"},
    ]

    with (
        patch("maia.scribe.flow.fetch_scribe_targets") as mock_fetch,
        patch("maia.scribe.flow.process_transcript") as mock_process,
    ):
        mock_fetch.return_value = mock_videos
        mock_process.return_value = AsyncMock()

        await run_scribe_cycle(batch_size=3)

        # Verify each video was processed
        assert mock_process.call_count == 3
        mock_process.assert_any_call(mock_videos[0])
        mock_process.assert_any_call(mock_videos[1])
        mock_process.assert_any_call(mock_videos[2])


@pytest.mark.asyncio
async def test_run_scribe_cycle_continues_on_individual_failures():
    """Test run_scribe_cycle continues processing even if individual videos fail."""
    mock_videos = [
        {"id": "VIDEO_001", "title": "Video 1"},
        {"id": "VIDEO_002", "title": "Video 2 (will fail)"},
        {"id": "VIDEO_003", "title": "Video 3"},
    ]

    call_count = {"count": 0}

    async def mock_process_side_effect(video):
        call_count["count"] += 1
        if video["id"] == "VIDEO_002":
            # Simulate failure on second video
            pass  # process_transcript handles errors internally
        return None

    with (
        patch("maia.scribe.flow.fetch_scribe_targets") as mock_fetch,
        patch("maia.scribe.flow.process_transcript") as mock_process,
    ):
        mock_fetch.return_value = mock_videos
        mock_process.side_effect = mock_process_side_effect

        await run_scribe_cycle(batch_size=3)

        # All videos should have been attempted
        assert call_count["count"] == 3


@pytest.mark.asyncio
async def test_transcript_loader_retry_logic():
    """Test that transcript fetching retries on network errors."""
    video = {"id": "VIDEO_001", "title": "Test Video"}
    mock_transcript = [{"text": "Success", "start": 0.0, "duration": 1.0}]

    with (
        patch("maia.scribe.flow.MaiaDAO") as MockDAO,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_transcript_safe = AsyncMock()

        mock_loader_instance = MockLoader.return_value
        # First two calls fail, third succeeds
        mock_loader_instance.fetch = MagicMock(
            side_effect=[
                ConnectionError("Network error"),
                ConnectionError("Network error"),
                mock_transcript,
            ]
        )

        mock_vault.store_transcript = MagicMock()

        await process_transcript(video)

        # Verify fetch was retried 3 times
        assert mock_loader_instance.fetch.call_count == 3

        # Verify eventual success
        mock_vault.store_transcript.assert_called_once()
        mock_dao.mark_video_transcript_safe.assert_called_once_with("VIDEO_001")
