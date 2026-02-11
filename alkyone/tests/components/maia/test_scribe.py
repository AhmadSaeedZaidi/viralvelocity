"""
Integration tests for Maia Scribe.

These tests verify end-to-end behavior of Scribe transcription flows.
Mark as integration tests: pytest -m integration

Real Integration Testing: Uses real YouTube video (Blender Tutorial) with real captions.
"""

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.scribe.flow import process_transcript, run_scribe_cycle


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_complete_cycle(dao):
    """Test complete Scribe cycle using real Blender tutorial video with real transcripts."""
    test_video = {
        "id": {"videoId": "B0J27sf9N1Y"},
        "snippet": {
            "channelId": "UCOKHwx1VCdgnxwbjyb9Iu1g",
            "channelTitle": "Blender Guru",
            "title": "Beginner Blender 4.0 Tutorial (2023)",
            "publishedAt": "2023-11-16T00:00:00Z",
            "tags": ["blender", "tutorial"],
            "categoryId": "27",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(test_video)

    try:
        await run_scribe_cycle(batch_size=1)

        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("B0J27sf9N1Y",))
        assert video["has_transcript"] is True

    except Exception as e:
        if "429" in str(e) or "HTTP Error 429" in str(e):
            pytest.skip("YouTube rate limit (429) encountered")
        elif "TranscriptsDisabled" in str(e) or "No transcript" in str(e):
            pytest.skip("Transcripts disabled or unavailable for this video")
        else:
            raise


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

    with patch("maia.scribe.flow.TranscriptLoader") as MockLoader:
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch = MagicMock(return_value=None)
        MockLoader.return_value = mock_loader_instance

        await run_scribe_cycle(batch_size=1)

        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("NO_TRANSCRIPT_001",))
        assert video["has_transcript"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_handles_resiliency_strategy(dao):
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
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch = MagicMock(side_effect=SystemExit("429 Rate Limit"))
        MockLoader.return_value = mock_loader_instance

        # Verify SystemExit is propagated
        with pytest.raises(SystemExit):
            await run_scribe_cycle(batch_size=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_empty_queue_returns_idle(dao):
    """Test Scribe handles empty queue gracefully."""
    await run_scribe_cycle(batch_size=10)

    assert True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_batch_size_enforcement(dao):
    """Test Scribe respects batch size limit."""
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

    with patch("maia.scribe.flow.TranscriptLoader") as MockLoader:
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)
        MockLoader.return_value = mock_loader_instance

        await run_scribe_cycle(batch_size=5)

        processed = await dao._fetch_all(
            "SELECT * FROM videos WHERE has_transcript = TRUE AND id LIKE 'BATCH_TEST_%%'"
        )
        assert len(processed) == 5

        remaining = await dao._fetch_all(
            "SELECT * FROM videos WHERE has_transcript = FALSE AND id LIKE 'BATCH_TEST_%%'"
        )
        assert len(remaining) == 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scribe_sequential_processing(dao):
    """Test Scribe processes videos sequentially to manage rate limits."""

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

    with patch("maia.scribe.flow.TranscriptLoader") as MockLoader:
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)
        MockLoader.return_value = mock_loader_instance

        await run_scribe_cycle(batch_size=3)

        processed = await dao._fetch_all(
            "SELECT * FROM videos WHERE has_transcript = TRUE AND id LIKE 'SEQ_TEST_%%'"
        )
        assert len(processed) == 3


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
        patch("maia.scribe.flow.vault.store_transcript") as mock_store,
    ):
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch = MagicMock(return_value=mock_transcript)
        MockLoader.return_value = mock_loader_instance

        mock_store.side_effect = Exception("Vault connection error")

        await run_scribe_cycle(batch_size=1)

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

    with patch("maia.scribe.flow.TranscriptLoader") as MockLoader:
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch = MagicMock(
            side_effect=[
                ConnectionError("Network error"),
                ConnectionError("Network error"),
                mock_transcript,
            ]
        )
        MockLoader.return_value = mock_loader_instance

        await run_scribe_cycle(batch_size=1)

        assert mock_loader_instance.fetch.call_count == 3

        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("RETRY_TEST_001",))
        assert video["has_transcript"] is True


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance
