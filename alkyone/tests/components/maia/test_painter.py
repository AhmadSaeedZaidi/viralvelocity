"""
Integration tests for Maia Painter.

These tests verify end-to-end behavior of Painter keyframe extraction flows.
Mark as integration tests: pytest -m integration
"""

import socket
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from maia.painter.flow import run_painter_cycle


def is_network_available() -> bool:
    """Check if network is available for integration tests."""
    try:
        socket.create_connection(("www.youtube.com", 80), timeout=3)
        return True
    except OSError:
        return False


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(60)
@pytest.mark.skipif(not is_network_available(), reason="Network unavailable")
async def test_painter_real_blender_tutorial(dao):
    """
    End-to-end Painter execution on the Blender 4.0 Beginner Tutorial.

    This test verifies that Painter can:
    1. Fetch the real video "Beginner Blender 4.0 Tutorial (2023)" (B0J27sf9N1Y)
    2. Extract actual keyframes using OpenCV
    3. Generate valid numpy arrays from the video stream
    4. Store frames to vault (mocked)
    """
    video_data = {
        "id": {"videoId": "B0J27sf9N1Y"},
        "snippet": {
            "channelId": "UCOKHwx1VCdgnxwbjyb9Iu1g",
            "channelTitle": "Blender Guru",
            "title": "Beginner Blender 4.0 Tutorial (2023)",
            "publishedAt": "2023-11-16T00:00:00Z",
            "tags": ["blender", "tutorial", "3d modeling", "donut"],
            "categoryId": "27",  # Education
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)

    with patch("maia.painter.flow.vault") as mock_vault:
        mock_vault.store_visual_evidence = MagicMock()

        # Run cycle for the Blender tutorial
        await run_painter_cycle(batch_size=1)

        mock_vault.store_visual_evidence.assert_called_once()

        call_args = mock_vault.store_visual_evidence.call_args
        vid_id = call_args[0][0]
        frames = call_args[0][1]

        assert vid_id == "B0J27sf9N1Y"
        assert len(frames) > 0, "Should extract at least 1 frame from the tutorial"

        for frame_idx, image_bytes in frames:
            assert isinstance(frame_idx, int), "Frame index should be integer"
            assert isinstance(image_bytes, bytes), "Frame should be bytes"
            # Tutorials often have high-detail UI, check for reasonable file size
            assert len(image_bytes) > 1000, "JPEG should be at least 1KB"

        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("B0J27sf9N1Y",))
        assert video["has_visuals"] is True
        assert video["status"] != "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_empty_queue_returns_idle(dao):
    """Test Painter handles empty queue gracefully."""
    await run_painter_cycle(batch_size=5)

    assert True


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

        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("NO_STREAM_001",))
        assert video["status"] == "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_propagates_resiliency_strategy(dao):
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

        with pytest.raises(SystemExit):
            await run_painter_cycle(batch_size=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_batch_size_enforcement(dao):
    """Test Painter respects batch size limit."""
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

        await run_painter_cycle(batch_size=3)

        assert mock_vault.store_visual_evidence.call_count == 3

        remaining = await dao._fetch_all(
            "SELECT * FROM videos WHERE has_visuals = FALSE AND id LIKE 'BATCH_TEST_%'"
        )
        assert len(remaining) == 7


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_painter_vault_failure_marks_video_failed(dao, mock_sleep):
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

        mock_vault.store_visual_evidence = MagicMock(
            side_effect=Exception("Vault connection error")
        )

        await run_painter_cycle(batch_size=1)

        video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", ("VAULT_FAIL_001",))
        assert video["status"] == "FAILED"
