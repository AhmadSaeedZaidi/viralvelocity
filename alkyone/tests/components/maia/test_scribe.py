"""
Integration tests for Maia Scribe.

These tests verify end-to-end behavior of Scribe transcription flows.
Mark as integration tests: pytest -m integration
"""

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.scribe.flow import process_transcript, run_scribe_cycle


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_complete_cycle(dao):
    """Test complete Scribe cycle: fetch videos, download transcripts, store to Vault."""
    # Setup: Insert test videos needing transcripts
    test_videos = [
        {
            "id": {"videoId": "SCRIBE_TEST_001"},
            "snippet": {
                "channelId": "CHANNEL_001",
                "channelTitle": "Test Channel",
                "title": "Video needing transcript",
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        },
        {
            "id": {"videoId": "SCRIBE_TEST_002"},
            "snippet": {
                "channelId": "CHANNEL_002",
                "channelTitle": "Test Channel 2",
                "title": "Another video needing transcript",
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        },
    ]

    for video_data in test_videos:
        await dao.ingest_video_metadata(video_data)

    # Mock transcript data
    mock_transcript = [
        {"text": "Hello", "start": 0.0, "duration": 1.0},
        {"text": "World", "start": 1.0, "duration": 1.0},
    ]

    with (
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)

        mock_vault.store_transcript = MagicMock()

        # Execute cycle
        await run_scribe_cycle(batch_size=2)

        # Verify transcripts were stored
        assert mock_vault.store_transcript.call_count == 2

        # Verify videos were marked as having transcripts
        video1 = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("SCRIBE_TEST_001",))
        video2 = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("SCRIBE_TEST_002",))

        assert video1["has_transcript"] is True
        assert video2["has_transcript"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_handles_unavailable_transcripts(dao):
    """Test Scribe handles videos with disabled transcripts."""
    video_data = {
        "id": {"videoId": "NO_TRANSCRIPT_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video without transcript",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    with (
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_loader_instance = MockLoader.return_value
        # Return None to indicate transcripts are disabled
        mock_loader_instance.fetch = MagicMock(return_value=None)

        mock_vault.store_transcript = MagicMock()

        await run_scribe_cycle(batch_size=1)

        # Verify vault was not called
        mock_vault.store_transcript.assert_not_called()

        # Verify video was still marked as processed
        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("NO_TRANSCRIPT_001",))
        assert video["has_transcript"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_handles_hydra_protocol(dao):
    """Test Scribe propagates SystemExit on rate limit (Resiliency Strategy)."""
    video_data = {
        "id": {"videoId": "RATE_LIMIT_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video causing rate limit",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    with patch("maia.scribe.flow.TranscriptLoader") as MockLoader:
        mock_loader_instance = MockLoader.return_value
        # Simulate TooManyRequests which raises SystemExit in TranscriptLoader
        mock_loader_instance.fetch = MagicMock(side_effect=SystemExit("429 Rate Limit"))

        # Verify SystemExit is propagated
        with pytest.raises(SystemExit):
            await run_scribe_cycle(batch_size=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_empty_queue_returns_idle(dao):
    """Test Scribe handles empty queue gracefully."""
    # No videos need transcripts
    await run_scribe_cycle(batch_size=10)

    # Should complete without errors (nothing to do)
    assert True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_batch_size_enforcement(dao):
    """Test Scribe respects batch size limit."""
    # Insert 10 videos needing transcripts
    for i in range(10):
        video_data = {
            "id": {"videoId": f"BATCH_TEST_{i:03d}"},
            "snippet": {
                "channelId": "CHANNEL_001",
                "channelTitle": "Test Channel",
                "title": f"Video {i}",
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        }
        await dao.ingest_video_metadata(video_data)

    mock_transcript = [{"text": "Test", "start": 0.0, "duration": 1.0}]

    with (
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)
        mock_vault.store_transcript = MagicMock()

        # Request batch of 5
        await run_scribe_cycle(batch_size=5)

        # Verify only 5 were processed
        assert mock_vault.store_transcript.call_count == 5

        # Verify remaining 5 are still pending
        remaining = await dao._fetch_all(
            "SELECT * FROM videos WHERE has_transcript = FALSE AND id LIKE 'BATCH_TEST_%'"
        )
        assert len(remaining) == 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_sequential_processing(dao):
    """Test Scribe processes videos sequentially to manage rate limits."""
    # Insert 3 videos
    for i in range(3):
        video_data = {
            "id": {"videoId": f"SEQ_TEST_{i:03d}"},
            "snippet": {
                "channelId": "CHANNEL_001",
                "channelTitle": "Test Channel",
                "title": f"Video {i}",
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        }
        await dao.ingest_video_metadata(video_data)

    processing_order = []
    mock_transcript = [{"text": "Test", "start": 0.0, "duration": 1.0}]

    def track_processing(vid_id, transcript_data):
        processing_order.append(vid_id)

    with (
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)
        mock_vault.store_transcript = MagicMock(side_effect=track_processing)

        await run_scribe_cycle(batch_size=3)

        # Verify all videos were processed
        assert len(processing_order) == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_vault_failure_marks_video_failed(dao):
    """Test Scribe marks video as failed when vault storage fails after retries."""
    video_data = {
        "id": {"videoId": "VAULT_FAIL_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video with vault failure",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    mock_transcript = [{"text": "Test", "start": 0.0, "duration": 1.0}]

    with (
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_loader_instance = MockLoader.return_value
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)

        # Vault fails all retries
        mock_vault.store_transcript = MagicMock(side_effect=Exception("Vault connection error"))

        await run_scribe_cycle(batch_size=1)

        # Verify video was marked as failed
        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("VAULT_FAIL_001",))
        assert video["status"] == "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_retry_logic_on_network_errors(dao):
    """Test Scribe retries transcript fetching on transient network errors."""
    video_data = {
        "id": {"videoId": "RETRY_TEST_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video with network issues",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    mock_transcript = [{"text": "Success", "start": 0.0, "duration": 1.0}]

    with (
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.vault") as mock_vault,
    ):
        mock_loader_instance = MockLoader.return_value
        # First two attempts fail, third succeeds
        mock_loader_instance.fetch = MagicMock(
            side_effect=[
                ConnectionError("Network error"),
                ConnectionError("Network error"),
                mock_transcript,
            ]
        )

        mock_vault.store_transcript = MagicMock()

        await run_scribe_cycle(batch_size=1)

        # Verify retries happened
        assert mock_loader_instance.fetch.call_count == 3

        # Verify eventual success
        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("RETRY_TEST_001",))
        assert video["has_transcript"] is True


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance
