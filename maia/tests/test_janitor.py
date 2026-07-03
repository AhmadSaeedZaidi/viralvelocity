"""
Tests for Maia Janitor module — State Machine cleanup cycle.
"""

from typing import Any, Dict
from unittest.mock import ANY, AsyncMock, patch

import pytest

from maia.janitor.flow import (
    handoff_phase_task,
    janitor_flow,
    log_summary_task,
    sweep_phase_task,
)


def _make_video(video_id: str, **overrides: Any) -> Any:
    from atlas.models import Video

    return Video(
        id=video_id,
        channel_id="mock_channel",
        title=f"Video {video_id}",
        status="PROCESSED",
        has_transcript=True,
        has_visuals=True,
        **overrides,
    )


def _make_video_dict(video_id: str, **overrides: Any) -> Dict[str, Any]:
    return _make_video(video_id, **overrides).model_dump()


@pytest.mark.asyncio
async def test_sweep_phase_empty():
    """Sweep returns empty list when no PROCESSED videos are eligible."""
    with patch("maia.janitor.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.count_archivable = AsyncMock(return_value=0)
        mock_repo.sweep_archivable = AsyncMock(return_value=[])

        result = await sweep_phase_task.fn(batch_size=50)

        assert result == []
        mock_repo.count_archivable.assert_called_once()
        mock_repo.sweep_archivable.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_phase_with_videos():
    """Sweep returns PROCESSED videos eligible for archival."""
    with patch("maia.janitor.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.count_archivable = AsyncMock(return_value=3)
        mock_repo.sweep_archivable = AsyncMock(
            return_value=[
                _make_video("VIDEO_001"),
                _make_video("VIDEO_002"),
                _make_video("VIDEO_003"),
            ]
        )

        result = await sweep_phase_task.fn(batch_size=50)

        assert len(result) == 3
        assert result[0]["id"] == "VIDEO_001"
        mock_repo.sweep_archivable.assert_called_once_with(batch_size=50)


@pytest.mark.asyncio
async def test_handoff_phase_dry_run():
    """Hand-off in dry-run mode returns would_archive count."""
    videos = [
        _make_video_dict("VIDEO_001"),
        _make_video_dict("VIDEO_002"),
    ]

    with patch("maia.janitor.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.archive_video_batch = AsyncMock(
            return_value={
                "archived": 0,
                "dry_run": True,
                "would_archive": 2,
                "video_ids": ["VIDEO_001", "VIDEO_002"],
                "failed": 0,
                "failed_ids": [],
            }
        )

        result = await handoff_phase_task.fn(videos, dry_run=True)

        assert result["dry_run"] is True
        assert result["would_archive"] == 2
        mock_repo.archive_video_batch.assert_called_once()


@pytest.mark.asyncio
async def test_janitor_flow_no_videos():
    """Full janitor cycle with no archivable videos completes gracefully."""
    with (
        patch("maia.janitor.flow.VideoRepository") as MockRepo,
        patch("maia.janitor.flow.archive_cold_stats_task", new_callable=AsyncMock) as mock_stats,
        patch("maia.janitor.flow.events") as mock_events,
    ):
        mock_events.emit = AsyncMock()
        mock_repo = MockRepo.return_value
        mock_repo.count_archivable = AsyncMock(return_value=0)
        mock_repo.sweep_archivable = AsyncMock(return_value=[])
        mock_stats.return_value = {"archived": 0, "batches": 0}

        result = await janitor_flow.fn(dry_run=False, archive_stats=True, batch_size=50)

        assert result["stats_archived"] == 0
        assert result["videos_archived"] == 0
        assert result["videos_failed"] == 0
        assert result["dry_run"] is False


@pytest.mark.asyncio
async def test_janitor_flow_happy_path():
    """Full janitor cycle succeeds: stats archived + videos archived."""
    mock_videos = [
        _make_video("VIDEO_001"),
        _make_video("VIDEO_002"),
    ]

    with (
        patch("maia.janitor.flow.VideoRepository") as MockRepo,
        patch("maia.janitor.flow.archive_cold_stats_task", new_callable=AsyncMock) as mock_stats,
        patch("maia.janitor.flow.events") as mock_events,
    ):
        mock_events.emit = AsyncMock()
        mock_repo = MockRepo.return_value
        mock_repo.count_archivable = AsyncMock(return_value=2)
        mock_repo.sweep_archivable = AsyncMock(return_value=mock_videos)
        mock_repo.archive_video_batch = AsyncMock(
            return_value={"archived": 2, "failed": 0, "failed_ids": []}
        )
        mock_stats.return_value = {"archived": 100, "batches": 1}

        result = await janitor_flow.fn(dry_run=False, archive_stats=True, batch_size=50)

        assert result["stats_archived"] == 100
        assert result["videos_archived"] == 2
        assert result["videos_failed"] == 0
        mock_repo.sweep_archivable.assert_called_once_with(batch_size=50)
        mock_repo.archive_video_batch.assert_called_once()


@pytest.mark.asyncio
async def test_janitor_flow_dry_run():
    """Dry-run mode does not execute stats archival or video hand-off."""
    with (
        patch("maia.janitor.flow.VideoRepository") as MockRepo,
        patch("maia.janitor.flow.archive_cold_stats_task", new_callable=AsyncMock) as mock_stats,
        patch("maia.janitor.flow.events") as mock_events,
    ):
        mock_events.emit = AsyncMock()
        mock_repo = MockRepo.return_value
        mock_repo.count_archivable = AsyncMock(return_value=0)
        mock_repo.sweep_archivable = AsyncMock(return_value=[])

        result = await janitor_flow.fn(dry_run=True, archive_stats=True, batch_size=50)

        assert result["dry_run"] is True
        mock_stats.assert_not_called()


@pytest.mark.asyncio
async def test_janitor_flow_failure_path():
    """When archive_video_batch reports failures, cycle still reports results."""
    mock_videos = [
        _make_video("VIDEO_FAIL_001"),
        _make_video("VIDEO_FAIL_002"),
    ]

    with (
        patch("maia.janitor.flow.VideoRepository") as MockRepo,
        patch("maia.janitor.flow.archive_cold_stats_task", new_callable=AsyncMock) as mock_stats,
        patch("maia.janitor.flow.events") as mock_events,
    ):
        mock_events.emit = AsyncMock()
        mock_repo = MockRepo.return_value
        mock_repo.count_archivable = AsyncMock(return_value=2)
        mock_repo.sweep_archivable = AsyncMock(return_value=mock_videos)
        mock_repo.archive_video_batch = AsyncMock(
            return_value={
                "archived": 0,
                "failed": 2,
                "failed_ids": ["VIDEO_FAIL_001", "VIDEO_FAIL_002"],
            }
        )
        mock_stats.return_value = {"archived": 50, "batches": 1}

        result = await janitor_flow.fn(dry_run=False, archive_stats=True, batch_size=50)

        assert result["videos_archived"] == 0
        assert result["videos_failed"] == 2


@pytest.mark.asyncio
async def test_log_summary_emits_event():
    """log_summary_task emits a janitor.cycle_complete event."""
    with patch("maia.janitor.flow.events") as mock_events:
        mock_events.emit = AsyncMock()
        results = {
            "stats_archived": 50,
            "videos_archived": 10,
            "videos_failed": 0,
            "dry_run": False,
        }

        await log_summary_task.fn(results)

        mock_events.emit.assert_called_once_with(
            "janitor.cycle_complete",
            "janitor",
            {
                "stats_archived": 50,
                "videos_archived": 10,
                "videos_failed": 0,
                "dry_run": False,
            },
        )


@pytest.mark.asyncio
async def test_archive_video_batch_delegates_to_repo():
    """handoff_phase_task correctly delegates archive_video_batch to repo."""
    videos = [_make_video_dict("VIDEO_001")]

    with patch("maia.janitor.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.archive_video_batch = AsyncMock(
            return_value={"archived": 1, "failed": 0, "failed_ids": []}
        )

        result = await handoff_phase_task.fn(videos, dry_run=False)

        assert result["archived"] == 1
        mock_repo.archive_video_batch.assert_called_once_with(
            ANY, dry_run=False
        )
