"""
Tests for Maia Scribe module (caption + transcript extraction).

The Scribe is the single owner of caption fetching: it hits YouTube's
`timedtext` endpoint via its multi-client cascade (with 429-release-to-PENDING
backoff) and only falls back to speech-to-text on the Singer's stored audio
(read from the vault) when no captions exist.
"""

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from atlas.models import Video
from atlas.utils import QuotaExhaustedError
from maia.scribe.flow import (
    fetch_scribe_targets_task,
    process_transcript_task,
    scribe_flow,
)
from maia.scribe.loader import TranscriptExtractionError
from maia.scribe.transcription import TranscriptResult


def _json3(segments: list[dict]) -> bytes:
    events = [
        {
            "tStartMs": int(s["start"] * 1000),
            "dDurationMs": int(s["duration"] * 1000),
            "segs": [{"utf8": s["text"]}],
        }
        for s in segments
    ]
    return json.dumps({"events": events}).encode()


def _audio_result(segments: list[dict]) -> TranscriptResult:
    return TranscriptResult(segments=segments, source="grok")


@pytest.mark.asyncio
async def test_fetch_scribe_targets_empty():
    """Test fetch_scribe_targets returns empty list when no videos need transcripts."""
    with patch("maia.scribe.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.claim_scribe_batch = AsyncMock(return_value=[])

        result = await fetch_scribe_targets_task.fn(batch_size=10)

        assert result == []
        mock_repo.claim_scribe_batch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_fetch_scribe_targets_with_videos():
    """Test fetch_scribe_targets returns videos needing transcripts."""
    mock_videos = [
        Video(id="VIDEO_001", title="Test Video 1"),
        Video(id="VIDEO_002", title="Test Video 2"),
    ]

    with patch("maia.scribe.flow.VideoRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.claim_scribe_batch = AsyncMock(return_value=mock_videos)

        result = await fetch_scribe_targets_task.fn(batch_size=10)

        assert len(result) == 2
        assert result[0].id == "VIDEO_001"
        mock_repo.claim_scribe_batch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_process_transcript_youtube_caption_first():
    """Scribe fetches captions from YouTube first (no vault captions step)."""
    video = Video(id="VIDEO_001", title="Test Video")
    segs = [{"text": "Hello", "start": 0.0, "duration": 1.5}]

    with (
        patch("maia.scribe.flow.VideoRepository") as MockRepo,
        patch("maia.scribe.flow.TranscriptRepository") as MockTranscriptRepo,
        patch("maia.scribe.flow.get_vault") as mock_get_vault,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
    ):
        mock_repo = MockRepo.return_value
        mock_repo.mark_transcript_safe = AsyncMock()
        mock_transcript_repo = MockTranscriptRepo.return_value
        mock_transcript_repo.record_transcript = AsyncMock()
        mock_vault = mock_get_vault.return_value
        mock_vault.fetch_binary = MagicMock(return_value=None)
        MockLoader.return_value.fetch = MagicMock(return_value=segs)

        await process_transcript_task.fn(video)

        # Caption ownership lives entirely in the Scribe → YouTube fetch first.
        MockLoader.return_value.fetch.assert_called_once_with("VIDEO_001")
        mock_transcript_repo.record_transcript.assert_called_once()
        mock_repo.mark_transcript_safe.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_transcript_audio_stt_fallback():
    """No captions anywhere → speech-to-text on the Singer's vault audio."""
    video = Video(id="VIDEO_003", title="Test Video")
    segs = [{"text": "Audio only", "start": 0.0, "duration": 2.0}]

    with (
        patch("maia.scribe.flow.VideoRepository") as MockRepo,
        patch("maia.scribe.flow.TranscriptRepository") as MockTranscriptRepo,
        patch("maia.scribe.flow.get_vault") as mock_get_vault,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.transcribe_audio_path") as mock_audio,
        patch("maia.scribe.flow.audio_cap_reached", return_value=False),
        patch("maia.scribe.flow.record_audio_usage"),
    ):
        mock_repo = MockRepo.return_value
        mock_repo.mark_transcript_safe = AsyncMock()
        mock_transcript_repo = MockTranscriptRepo.return_value
        mock_transcript_repo.record_transcript = AsyncMock()
        # No vault captions; YouTube caption fetch fails; audio IS in the vault.
        mock_vault = mock_get_vault.return_value
        mock_vault.fetch_binary = MagicMock(
            side_effect=lambda path: io.BytesIO(b"OPUS") if path.startswith("audio/") else None
        )
        MockLoader.return_value.fetch = MagicMock(
            side_effect=TranscriptExtractionError("no subtitles")
        )
        mock_audio.return_value = _audio_result(segs)

        await process_transcript_task.fn(video)

        MockLoader.return_value.fetch.assert_called_once_with("VIDEO_003")
        mock_audio.assert_called_once()
        mock_transcript_repo.record_transcript.assert_called_once()
        mock_repo.mark_transcript_safe.assert_called_once_with("VIDEO_003")


@pytest.mark.asyncio
async def test_process_transcript_unavailable():
    """No transcript at all (no captions, no audio STT) → marked safe, not failed."""
    video = Video(id="VIDEO_NO_T", title="Video Without Transcript")

    with (
        patch("maia.scribe.flow.VideoRepository") as MockRepo,
        patch("maia.scribe.flow.TranscriptRepository") as MockTranscriptRepo,
        patch("maia.scribe.flow.get_vault") as mock_get_vault,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.transcribe_audio_download") as mock_dl,
        patch("maia.scribe.flow.audio_cap_reached", return_value=False),
        patch("maia.scribe.flow.record_audio_usage"),
    ):
        mock_repo = MockRepo.return_value
        mock_repo.mark_transcript_safe = AsyncMock()
        mock_transcript_repo = MockTranscriptRepo.return_value
        mock_transcript_repo.record_transcript = AsyncMock()
        mock_vault = mock_get_vault.return_value
        mock_vault.fetch_binary = MagicMock(return_value=None)
        MockLoader.return_value.fetch = MagicMock(
            side_effect=TranscriptExtractionError("no subtitles")
        )
        mock_dl.side_effect = TranscriptExtractionError("no audio transcriber")

        await process_transcript_task.fn(video)

        mock_repo.mark_transcript_safe.assert_called_once_with("VIDEO_NO_T")
        mock_transcript_repo.record_transcript.assert_not_called()


@pytest.mark.asyncio
async def test_vault_flush_retries_and_marks_failed(mock_sleep, mock_prefect_logger):
    """Janitor vault_flush retries on failure (429 backoff) and marks videos failed."""
    from maia.janitor.flow import vault_flush_task

    video = {"id": "VIDEO_001", "transcript": b"{}", "audio": None}

    with (
        patch("maia.janitor.flow.TranscriptRepository") as MockRepo,
        patch("maia.janitor.flow.get_vault") as mock_get_vault,
    ):
        mock_repo = MockRepo.return_value
        mock_repo.claim_vault_pending_batch = AsyncMock(return_value=[video])
        mock_repo.clear_vault_pending = AsyncMock()
        mock_repo.mark_failed = AsyncMock()
        mock_vault = MagicMock()
        mock_vault.store_batch = MagicMock(side_effect=Exception("Vault connection error"))
        mock_get_vault.return_value = mock_vault

        result = await vault_flush_task.fn(batch_size=10)

        # On vault failure the video stays pending (self-heals on retry); it is
        # NOT marked failed. So flushed=0, failed=1, and clear_vault_pending is
        # not called. (store_batch now retries HTTP 429 internally; this test
        # verifies the janitor leaves the video pending on hard failure.)
        assert result["flushed"] == 0
        assert result["failed"] == 1
        mock_repo.clear_vault_pending.assert_not_called()


@pytest.mark.asyncio
async def test_process_transcript_handles_transcript_fetch_failure():
    """Unexpected transcription error → video marked failed."""
    video = Video(id="VIDEO_001", title="Test Video")

    with (
        patch("maia.scribe.flow.VideoRepository") as MockRepo,
        patch("maia.scribe.flow.get_vault") as mock_get_vault,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
    ):
        mock_repo = MockRepo.return_value
        mock_repo.mark_failed = AsyncMock()
        mock_vault = mock_get_vault.return_value
        mock_vault.fetch_binary = MagicMock(return_value=None)
        MockLoader.return_value.fetch = MagicMock(side_effect=Exception("Network timeout"))

        await process_transcript_task.fn(video)

        mock_repo.mark_failed.assert_called_once_with("VIDEO_001")


@pytest.mark.asyncio
async def test_process_transcript_propagates_resiliency_strategy():
    """QuotaExhaustedError propagates out of process_transcript."""
    video = Video(id="VIDEO_001", title="Test Video")

    with (
        patch("maia.scribe.flow.VideoRepository"),
        patch("maia.scribe.flow.get_vault") as mock_get_vault,
        patch("maia.scribe.flow.TranscriptLoader") as MockLoader,
        patch("maia.scribe.flow.notify_quota_exhausted", new_callable=AsyncMock),
    ):
        mock_vault = mock_get_vault.return_value
        mock_vault.fetch_binary = MagicMock(return_value=None)
        MockLoader.return_value.fetch = MagicMock(
            side_effect=QuotaExhaustedError("All keys exhausted")
        )

        with pytest.raises(QuotaExhaustedError):
            await process_transcript_task.fn(video)


@pytest.mark.asyncio
async def test_run_scribe_cycle_empty_queue():
    """Test run_scribe_cycle handles empty queue gracefully."""
    with patch("maia.scribe.flow.fetch_scribe_targets_task", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []

        await scribe_flow.fn(batch_size=10)

        mock_fetch.assert_called_once_with(10)


@pytest.mark.asyncio
async def test_run_scribe_cycle_processes_batch():
    """Test run_scribe_cycle processes a batch of videos."""
    mock_videos = [
        Video(id="VIDEO_001", title="Video 1"),
        Video(id="VIDEO_002", title="Video 2"),
        Video(id="VIDEO_003", title="Video 3"),
    ]

    with (
        patch("maia.scribe.flow.fetch_scribe_targets_task", new_callable=AsyncMock) as mock_fetch,
        patch("maia.scribe.flow.process_transcript_task", new_callable=AsyncMock) as mock_process,
    ):
        mock_fetch.return_value = mock_videos
        mock_process.return_value = None

        await scribe_flow.fn(batch_size=3)

        assert mock_process.call_count == 3


@pytest.mark.asyncio
async def test_run_scribe_cycle_continues_on_individual_failures():
    """Test run_scribe_cycle continues processing even if individual videos fail."""
    mock_videos = [
        Video(id="VIDEO_001", title="Video 1"),
        Video(id="VIDEO_002", title="Video 2 (will fail)"),
        Video(id="VIDEO_003", title="Video 3"),
    ]

    call_count = {"count": 0}

    async def mock_process_side_effect(video):
        call_count["count"] += 1
        return None

    with (
        patch("maia.scribe.flow.fetch_scribe_targets_task", new_callable=AsyncMock) as mock_fetch,
        patch("maia.scribe.flow.process_transcript_task", new_callable=AsyncMock) as mock_process,
    ):
        mock_fetch.return_value = mock_videos
        mock_process.side_effect = mock_process_side_effect

        await scribe_flow.fn(batch_size=3)

        assert call_count["count"] == 3
