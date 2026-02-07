"""
Tests for Maia Painter module.
"""

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from maia.painter.flow import (
    VideoStreamer,
    fetch_painter_targets,
    process_frames,
    run_painter_cycle,
)


@pytest.mark.asyncio
async def test_fetch_painter_targets_empty():
    """Test fetch_painter_targets returns empty list when no videos need visual processing."""
    with patch("maia.painter.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_painter_batch = AsyncMock(return_value=[])

        result = await fetch_painter_targets(batch_size=5)

        assert result == []
        mock_dao.fetch_painter_batch.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_fetch_painter_targets_with_videos():
    """Test fetch_painter_targets returns videos needing visual processing."""
    mock_videos = [
        {"id": "VIDEO_001", "title": "Test Video 1"},
        {"id": "VIDEO_002", "title": "Test Video 2"},
    ]

    with patch("maia.painter.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_painter_batch = AsyncMock(return_value=mock_videos)

        result = await fetch_painter_targets(batch_size=5)

        assert len(result) == 2
        assert result[0]["id"] == "VIDEO_001"
        mock_dao.fetch_painter_batch.assert_called_once_with(5)


def test_video_streamer_extract_heatmap_peaks():
    """Test VideoStreamer extracts top N peaks from heatmap data."""
    streamer = VideoStreamer("VIDEO_001")

    heatmap_data = [
        {"start_time": 10.0, "end_time": 11.0, "value": 0.5},
        {"start_time": 25.0, "end_time": 26.0, "value": 0.9},  # Peak 1
        {"start_time": 50.0, "end_time": 51.0, "value": 0.3},
        {"start_time": 75.0, "end_time": 76.0, "value": 0.8},  # Peak 2
        {"start_time": 100.0, "end_time": 101.0, "value": 0.7},  # Peak 3
    ]

    peaks = streamer.extract_heatmap_peaks(heatmap_data, top_n=3)

    assert len(peaks) == 3
    assert peaks[0] == 25.0  # Highest value
    assert peaks[1] == 75.0  # Second highest
    assert peaks[2] == 100.0  # Third highest


def test_video_streamer_extract_heatmap_peaks_empty():
    """Test VideoStreamer handles empty heatmap data."""
    streamer = VideoStreamer("VIDEO_001")

    peaks = streamer.extract_heatmap_peaks([], top_n=5)

    assert peaks == []


@pytest.mark.asyncio
async def test_process_frames_successful_with_chapters():
    """Test process_frames successfully extracts frames using chapter strategy."""
    video = {"id": "VIDEO_001", "title": "Test Video with Chapters"}

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [
            {"start_time": 0.0, "title": "Intro"},
            {"start_time": 60.0, "title": "Main Content"},
            {"start_time": 120.0, "title": "Conclusion"},
        ],
        "heatmap": [],
    }

    # Create a mock frame (black image)
    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_visuals_safe = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        mock_cap_instance.get.side_effect = lambda prop: (
            30.0 if prop == 5 else 4500
        )  # FPS=30, frames=4500
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        await process_frames(video)

        # Verify frames were extracted and stored
        mock_vault.store_visual_evidence.assert_called_once()
        stored_frames = mock_vault.store_visual_evidence.call_args[0][1]
        assert len(stored_frames) == 3  # 3 chapters

        # Verify marked as safe
        mock_dao.mark_video_visuals_safe.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_frames_successful_with_heatmap():
    """Test process_frames successfully extracts frames using heatmap strategy."""
    video = {"id": "VIDEO_002", "title": "Test Video with Heatmap"}

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
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_visuals_safe = AsyncMock()

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

        await process_frames(video)

        # Verify heatmap peaks were extracted
        mock_streamer_instance.extract_heatmap_peaks.assert_called_once()
        mock_vault.store_visual_evidence.assert_called_once()

        mock_dao.mark_video_visuals_safe.assert_called_once_with("VIDEO_002")


@pytest.mark.asyncio
async def test_process_frames_fallback_strategy():
    """Test process_frames uses fallback strategy when no chapters/heatmap."""
    video = {"id": "VIDEO_003", "title": "Video without chapters or heatmap"}

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_visuals_safe = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)
        mock_streamer_instance.extract_heatmap_peaks = MagicMock(return_value=[])

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = True
        # 300 second video (5 minutes) at 30 FPS
        mock_cap_instance.get.side_effect = lambda prop: 30.0 if prop == 5 else 9000
        mock_cap_instance.read.return_value = (True, mock_frame)
        mock_cap_instance.set.return_value = None
        mock_cap_instance.release.return_value = None

        MockCapture.return_value = mock_cap_instance

        mock_vault.store_visual_evidence = MagicMock()

        await process_frames(video)

        # Verify fallback strategy (5 frames for short video)
        mock_vault.store_visual_evidence.assert_called_once()
        stored_frames = mock_vault.store_visual_evidence.call_args[0][1]
        assert len(stored_frames) == 5  # Default for short videos

        mock_dao.mark_video_visuals_safe.assert_called_once_with("VIDEO_003")


@pytest.mark.asyncio
async def test_process_frames_handles_no_stream_url():
    """Test process_frames handles videos with no stream URL."""
    video = {"id": "VIDEO_NO_STREAM", "title": "Video without stream"}

    mock_video_info = {"url": None, "chapters": [], "heatmap": []}

    with (
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_failed = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        await process_frames(video)

        # Verify video was marked as failed
        mock_dao.mark_video_failed.assert_called_once_with("VIDEO_NO_STREAM")


@pytest.mark.asyncio
async def test_process_frames_handles_video_capture_failure():
    """Test process_frames handles VideoCapture open failures."""
    video = {"id": "VIDEO_001", "title": "Test Video"}

    mock_video_info = {"url": "https://example.com/video.mp4", "chapters": [], "heatmap": []}

    with (
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_failed = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(return_value=mock_video_info)

        mock_cap_instance = MagicMock()
        mock_cap_instance.isOpened.return_value = False
        MockCapture.return_value = mock_cap_instance

        await process_frames(video)

        # Verify video was marked as failed
        mock_dao.mark_video_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_frames_handles_vault_failure():
    """Test process_frames handles vault storage failures after retries."""
    video = {"id": "VIDEO_001", "title": "Test Video"}

    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "chapters": [{"start_time": 0.0}],
        "heatmap": [],
    }

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    with (
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
        patch("maia.painter.flow.cv2.VideoCapture") as MockCapture,
        patch("maia.painter.flow.vault") as mock_vault,
    ):
        mock_dao = MockDAO.return_value
        mock_dao.mark_video_failed = AsyncMock()

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
        mock_vault.store_visual_evidence = MagicMock(
            side_effect=Exception("Vault connection error")
        )

        await process_frames(video)

        # Verify video was marked as failed
        mock_dao.mark_video_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_frames_propagates_hydra_protocol():
    """Test process_frames propagates SystemExit for Resiliency Strategy."""
    video = {"id": "VIDEO_001", "title": "Test Video"}

    with (
        patch("maia.painter.flow.MaiaDAO") as MockDAO,
        patch("maia.painter.flow.VideoStreamer") as MockStreamer,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.get_info = MagicMock(side_effect=SystemExit("429 Rate Limit"))

        with pytest.raises(SystemExit):
            await process_frames(video)


@pytest.mark.asyncio
async def test_run_painter_cycle_empty_queue():
    """Test run_painter_cycle handles empty queue gracefully."""
    with patch("maia.painter.flow.fetch_painter_targets") as mock_fetch:
        mock_fetch.return_value = []

        # Should complete without errors
        await run_painter_cycle(batch_size=5)

        mock_fetch.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_run_painter_cycle_processes_batch():
    """Test run_painter_cycle processes a batch of videos sequentially."""
    mock_videos = [
        {"id": "VIDEO_001", "title": "Video 1"},
        {"id": "VIDEO_002", "title": "Video 2"},
    ]

    with (
        patch("maia.painter.flow.fetch_painter_targets") as mock_fetch,
        patch("maia.painter.flow.process_frames") as mock_process,
    ):
        mock_fetch.return_value = mock_videos
        mock_process.return_value = AsyncMock()

        await run_painter_cycle(batch_size=2)

        # Verify each video was processed
        assert mock_process.call_count == 2
        mock_process.assert_any_call(mock_videos[0])
        mock_process.assert_any_call(mock_videos[1])
