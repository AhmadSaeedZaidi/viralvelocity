"""
Integration tests for Maia Painter.

These tests verify end-to-end behavior of Painter keyframe extraction flows.
Mark as integration tests: pytest -m integration
"""

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import numpy as np

from maia.painter.flow import run_painter_cycle, process_frames


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_complete_cycle(dao):
    """Test complete Painter cycle: fetch videos, extract frames, store to Vault."""
    # Setup: Insert test videos needing visual processing
    test_videos = [
        {
            "id": {"videoId": "PAINTER_TEST_001"},
            "snippet": {
                "channelId": "CHANNEL_001",
                "channelTitle": "Test Channel",
                "title": "Video needing frames",
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        },
        {
            "id": {"videoId": "PAINTER_TEST_002"},
            "snippet": {
                "channelId": "CHANNEL_002",
                "channelTitle": "Test Channel 2",
                "title": "Another video needing frames",
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        },
    ]

    for video_data in test_videos:
        await dao.ingest_video_metadata(video_data)

    # Mock video info and frames
    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [
            {"start_time": 0.0, "title": "Intro"},
            {"start_time": 60.0, "title": "Main"},
        ],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 3600
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        # Execute cycle
        await run_painter_cycle(batch_size=2)

        # Verify frames were stored
        assert mock_vault.store_visual_evidence.call_count == 2

        # Verify videos were marked as having visuals
        video1 = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("PAINTER_TEST_001",))
        video2 = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("PAINTER_TEST_002",))

        assert video1["has_visuals"] is True
        assert video2["has_visuals"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_chapter_based_extraction(dao):
    """Test Painter extracts frames at chapter boundaries."""
    video_data = {
        "id": {"videoId": "CHAPTER_TEST_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video with chapters",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [
            {"start_time": 0.0, "title": "Intro"},
            {"start_time": 120.0, "title": "Main Content"},
            {"start_time": 240.0, "title": "Conclusion"},
        ],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 9000
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        await run_painter_cycle(batch_size=1)

        # Verify frames were extracted at chapter points
        stored_frames = mock_vault.store_visual_evidence.call_args[0][1]
        assert len(stored_frames) == 3  # 3 chapters


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_heatmap_peak_extraction(dao):
    """Test Painter extracts frames at viral peaks from heatmap."""
    video_data = {
        "id": {"videoId": "HEATMAP_TEST_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video with heatmap",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [],
        "heatmap": [
            {"start_time": 10.0, "value": 0.9},
            {"start_time": 50.0, "value": 0.8},
            {"start_time": 90.0, "value": 0.7},
            {"start_time": 120.0, "value": 0.6},
            {"start_time": 150.0, "value": 0.5},
        ],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)
        mock_streamer_instance.extract_heatmap_peaks = MagicMock(
            return_value=[10.0, 50.0, 90.0, 120.0, 150.0]
        )

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 6000
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        await run_painter_cycle(batch_size=1)

        # Verify heatmap peaks were extracted
        mock_streamer_instance.extract_heatmap_peaks.assert_called_once()
        stored_frames = mock_vault.store_visual_evidence.call_args[0][1]
        assert len(stored_frames) == 5  # Top 5 peaks


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_fallback_strategy_for_long_video(dao):
    """Test Painter uses scaled fallback strategy for videos without chapters/heatmap."""
    video_data = {
        "id": {"videoId": "FALLBACK_TEST_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Long video without metadata",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)
        mock_streamer_instance.extract_heatmap_peaks = MagicMock(return_value=[])

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        # 35 minute video (2100 seconds) at 30 FPS
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 63000
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        await run_painter_cycle(batch_size=1)

        # Verify fallback strategy (20 frames for >30min video)
        stored_frames = mock_vault.store_visual_evidence.call_args[0][1]
        assert len(stored_frames) == 20  # Scaled for long video


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_handles_missing_stream_url(dao):
    """Test Painter marks video as failed when stream URL is unavailable."""
    video_data = {
        "id": {"videoId": "NO_STREAM_001"},
        "snippet": {
            "channelId": "CHANNEL_001",
            "channelTitle": "Test Channel",
            "title": "Video without stream",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "tags": ["test"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    mock_video_info = {"url": None, "chapters": [], "heatmap": []}

    with patch("maia.painter.flow.VideoStreamer") as MockStreamer:
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        await run_painter_cycle(batch_size=1)

        # Verify video was marked as failed
        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("NO_STREAM_001",))
        assert video["status"] == "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_handles_hydra_protocol(dao):
    """Test Painter propagates SystemExit for Resiliency Strategy."""
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

    with patch("maia.painter.flow.VideoStreamer") as MockStreamer:
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(side_effect=SystemExit("429 Rate Limit"))

        # Verify SystemExit is propagated
        with pytest.raises(SystemExit):
            await run_painter_cycle(batch_size=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_empty_queue_returns_idle(dao):
    """Test Painter handles empty queue gracefully."""
    # No videos need visual processing
    await run_painter_cycle(batch_size=5)

    # Should complete without errors (nothing to do)
    assert True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_batch_size_enforcement(dao):
    """Test Painter respects batch size limit."""
    # Insert 10 videos needing visual processing
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

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [{"start_time": 0.0}],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 900
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        # Request batch of 3
        await run_painter_cycle(batch_size=3)

        # Verify only 3 were processed
        assert mock_vault.store_visual_evidence.call_count == 3

        # Verify remaining 7 are still pending
        remaining = await dao._fetch_all(
            "SELECT * FROM videos WHERE has_visuals = FALSE AND id LIKE 'BATCH_TEST_%'"
        )
        assert len(remaining) == 7


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_vault_failure_marks_video_failed(dao):
    """Test Painter marks video as failed when vault storage fails after retries."""
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

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [{"start_time": 0.0}],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 900
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        # Vault fails all retries
        mock_vault.store_visual_evidence = MagicMock(side_effect=Exception("Vault connection error"))

        await run_painter_cycle(batch_size=1)

        # Verify video was marked as failed
        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("VAULT_FAIL_001",))
        assert video["status"] == "FAILED"


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance
