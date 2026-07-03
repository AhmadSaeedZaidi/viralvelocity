"""
Tests for Maia Painter module.
"""

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from maia.painter.flow import (
    fetch_painter_targets_task,
    painter_flow,
    process_frames_task,
)
from maia.painter.streamer import StealthVideoStreamer
from maia.utils import RateLimitError


@pytest.mark.asyncio
async def test_fetch_painter_targets_empty():
    """Test fetch_painter_targets returns empty list when no videos need visual processing."""
    with patch("maia.painter.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.claim_painter_batch = AsyncMock(return_value=[])

        result = await fetch_painter_targets_task.fn(batch_size=5)

        assert result == []
        mock_repo.claim_painter_batch.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_fetch_painter_targets_with_videos():
    """Test fetch_painter_targets returns videos needing visual processing."""
    from atlas.models import Video

    mock_videos = [
        Video(id="VIDEO_001", title="Test Video 1"),
        Video(id="VIDEO_002", title="Test Video 2"),
    ]

    with patch("maia.painter.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.claim_painter_batch = AsyncMock(return_value=mock_videos)

        result = await fetch_painter_targets_task.fn(batch_size=5)

        assert len(result) == 2
        assert result[0].id == "VIDEO_001"
        mock_repo.claim_painter_batch.assert_called_once_with(5)


def test_video_streamer_extract_heatmap_peaks():
    """Test StealthVideoStreamer extracts top N peaks from heatmap data."""
    streamer = StealthVideoStreamer()
    heatmap_data = [
        {"start_time": 10.0, "end_time": 11.0, "value": 0.5},
        {"start_time": 25.0, "end_time": 26.0, "value": 0.9},
        {"start_time": 50.0, "end_time": 51.0, "value": 0.3},
        {"start_time": 75.0, "end_time": 76.0, "value": 0.8},
        {"start_time": 100.0, "end_time": 101.0, "value": 0.7},
    ]
    peaks = streamer.extract_heatmap_peaks(heatmap_data, top_n=3)
    assert len(peaks) == 3
    assert peaks[0] == 25.0
    assert peaks[1] == 75.0
    assert peaks[2] == 100.0


def test_video_streamer_extract_heatmap_peaks_empty():
    """Test StealthVideoStreamer handles empty heatmap data."""
    streamer = StealthVideoStreamer()
    peaks = streamer.extract_heatmap_peaks([], top_n=5)
    assert peaks == []


@pytest.mark.asyncio
async def test_process_frames_successful_with_chapters():
    """Test process_frames successfully extracts frames using chapter strategy."""
    from atlas.models import Video

    video = Video(id="VIDEO_001", title="Test Video with Chapters")
    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "duration": 200.0,
        "chapters": [
            {"start_time": 0.0, "title": "Intro"},
            {"start_time": 60.0, "title": "Main Content"},
            {"start_time": 120.0, "title": "Conclusion"},
        ],
        "heatmap": [],
    }
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
        patch("maia.painter.flow.subprocess.Popen") as mock_popen,
        patch("maia.painter.flow.get_vault") as mock_get_vault,
        patch(
            "maia.painter.flow.vault_op_with_retry", new_callable=AsyncMock
        ) as mock_vault_retry,
    ):
        mock_vault = mock_get_vault.return_value
        mock_repo = MockRepo.return_value
        mock_repo.mark_visuals_safe = AsyncMock()
        mock_repo.mark_failed = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(return_value=mock_video_info)

        mock_process = MagicMock()
        mock_process.communicate.return_value = (fake_jpeg, b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        mock_vault.store_visual_evidence = MagicMock()

        await process_frames_task.fn(video)

        mock_vault_retry.assert_called_once()
        mock_repo.mark_visuals_safe.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_frames_successful_with_heatmap():
    """Test process_frames successfully extracts frames using heatmap strategy."""
    from atlas.models import Video

    video = Video(id="VIDEO_002", title="Test Video with Heatmap")
    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "duration": 200.0,
        "chapters": [],
        "heatmap": [
            {"start_time": 10.0, "value": 0.9},
        ],
    }
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
        patch("maia.painter.flow.subprocess.Popen") as mock_popen,
        patch("maia.painter.flow.get_vault") as mock_get_vault,
        patch(
            "maia.painter.flow.vault_op_with_retry", new_callable=AsyncMock
        ) as mock_vault_retry,
    ):
        mock_vault = mock_get_vault.return_value
        mock_repo = MockRepo.return_value
        mock_repo.mark_visuals_safe = AsyncMock()
        mock_repo.mark_failed = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(return_value=mock_video_info)
        mock_streamer_instance.extract_heatmap_peaks = MagicMock(return_value=[10.0])

        mock_process = MagicMock()
        mock_process.communicate.return_value = (fake_jpeg, b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        mock_vault.store_visual_evidence = MagicMock()

        await process_frames_task.fn(video)

        mock_vault_retry.assert_called_once()
        mock_repo.mark_visuals_safe.assert_called_once_with("VIDEO_002")


@pytest.mark.asyncio
async def test_process_frames_fallback_strategy():
    """Test process_frames uses fallback strategy when no chapters/heatmap."""
    from atlas.models import Video

    video = Video(id="VIDEO_003", title="Video without chapters or heatmap")
    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "duration": 600.0,
        "chapters": [],
        "heatmap": [],
    }
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
        patch("maia.painter.flow.subprocess.Popen") as mock_popen,
        patch("maia.painter.flow.get_vault") as mock_get_vault,
        patch(
            "maia.painter.flow.vault_op_with_retry", new_callable=AsyncMock
        ) as mock_vault_retry,
    ):
        mock_vault = mock_get_vault.return_value
        mock_repo = MockRepo.return_value
        mock_repo.mark_visuals_safe = AsyncMock()
        mock_repo.mark_failed = AsyncMock()

        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(return_value=mock_video_info)
        mock_streamer_instance.extract_heatmap_peaks = MagicMock(return_value=[])

        mock_process = MagicMock()
        mock_process.communicate.return_value = (fake_jpeg, b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        mock_vault.store_visual_evidence = MagicMock()

        await process_frames_task.fn(video)

        mock_vault_retry.assert_called_once()
        mock_repo.mark_visuals_safe.assert_called_once_with("VIDEO_003")


@pytest.mark.asyncio
async def test_process_frames_handles_no_stream_url():
    """Test process_frames handles videos with no stream URL."""
    from atlas.models import Video

    video = Video(id="VIDEO_NO_STREAM", title="Video without stream")
    mock_video_info = {"url": None, "chapters": [], "heatmap": []}

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
    ):
        mock_repo = MockRepo.return_value
        mock_repo.mark_failed = AsyncMock()
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(return_value=mock_video_info)

        await process_frames_task.fn(video)

        mock_repo.mark_failed.assert_called_once_with("VIDEO_NO_STREAM")


@pytest.mark.asyncio
async def test_process_frames_handles_video_capture_failure():
    """Test process_frames handles FFmpeg extraction failures."""
    from atlas.models import Video

    video = Video(id="VIDEO_001", title="Test Video")
    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "duration": 100.0,
        "chapters": [{"start_time": 0.0}],
        "heatmap": [],
    }

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
        patch("maia.painter.flow.subprocess.Popen") as mock_popen,
    ):
        mock_repo = MockRepo.return_value
        mock_repo.mark_failed = AsyncMock()
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(return_value=mock_video_info)

        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"", b"FFmpeg error")
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        await process_frames_task.fn(video)

        mock_repo.mark_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_frames_handles_vault_failure():
    """Test process_frames handles vault storage failures after retries."""
    from atlas.models import Video

    video = Video(id="VIDEO_001", title="Test Video")
    mock_video_info = {
        "url": "https://example.com/video.mp4",
        "duration": 100.0,
        "chapters": [{"start_time": 0.0}],
        "heatmap": [],
    }
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
        patch("maia.painter.flow.subprocess.Popen") as mock_popen,
        patch("maia.painter.flow.get_vault") as mock_get_vault,
        patch(
            "maia.painter.flow.vault_op_with_retry",
            new_callable=AsyncMock,
            side_effect=Exception("Vault error"),
        ),
    ):
        mock_vault = mock_get_vault.return_value
        mock_repo = MockRepo.return_value
        mock_repo.mark_failed = AsyncMock()
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(return_value=mock_video_info)

        mock_process = MagicMock()
        mock_process.communicate.return_value = (fake_jpeg, b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        await process_frames_task.fn(video)

        mock_repo.mark_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_frames_propagates_resiliency_strategy():
    """Test process_frames propagates RateLimitError for Resiliency Strategy."""
    from atlas.models import Video

    video = Video(id="VIDEO_001", title="Test Video")

    with (
        patch("maia.painter.flow.VideoRepository") as MockRepo,
        patch("maia.painter.flow.StealthVideoStreamer") as MockStreamer,
    ):
        mock_streamer_instance = MockStreamer.return_value
        mock_streamer_instance.extract_info = MagicMock(
            side_effect=RateLimitError("429 Rate Limit")
        )

        with pytest.raises(RateLimitError):
            await process_frames_task.fn(video)


@pytest.mark.asyncio
async def test_run_painter_cycle_empty_queue():
    """Test run_painter_cycle handles empty queue gracefully."""
    with patch(
        "maia.painter.flow.fetch_painter_targets_task", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = []

        await painter_flow.fn(batch_size=5)

        mock_fetch.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_run_painter_cycle_processes_batch():
    """Test run_painter_cycle processes a batch of videos sequentially."""
    from atlas.models import Video

    mock_videos = [
        Video(id="VIDEO_001", title="Video 1"),
        Video(id="VIDEO_002", title="Video 2"),
    ]

    with (
        patch(
            "maia.painter.flow.fetch_painter_targets_task", new_callable=AsyncMock
        ) as mock_fetch,
        patch(
            "maia.painter.flow.process_frames_task", new_callable=AsyncMock
        ) as mock_process,
    ):
        mock_fetch.return_value = mock_videos
        mock_process.return_value = None

        await painter_flow.fn(batch_size=2)

        assert mock_process.call_count == 2
        mock_process.assert_any_call(mock_videos[0])
        mock_process.assert_any_call(mock_videos[1])
