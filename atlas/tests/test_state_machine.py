"""Tests for the VideoStateMixin streamer/singer claim + mark methods."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from atlas.models import Video
from atlas.repositories.video.state_machine import VideoStateMixin


class FakeState(VideoStateMixin):
    """VideoStateMixin with the low-level DB helpers stubbed out."""

    def __init__(self) -> None:
        self._fetch_all = AsyncMock()
        self._fetch_one = AsyncMock()
        self._execute = AsyncMock()


@pytest.mark.asyncio
async def test_claim_streamer_batch():
    s = FakeState()
    s._fetch_all.return_value = [{"id": "V1", "title": "t", "has_audio": False}]
    vids = await s.claim_streamer_batch(5)
    s._fetch_all.assert_called_once()
    assert len(vids) == 1
    assert vids[0].id == "V1"
    assert isinstance(vids[0], Video)


@pytest.mark.asyncio
async def test_claim_singer_batch():
    s = FakeState()
    s._fetch_all.return_value = [
        {"id": "V1", "title": "t", "has_audio": True, "has_transcript": False}
    ]
    vids = await s.claim_singer_batch(5)
    s._fetch_all.assert_called_once()
    assert vids[0].id == "V1"


@pytest.mark.asyncio
async def test_mark_audio_safe():
    s = FakeState()
    await s.mark_audio_safe("V1")
    s._execute.assert_called_once()
    sql = s._execute.call_args[0][0]
    assert "has_audio = TRUE" in sql
    # Idempotent: a DONE step is never re-marked.
    assert "audio_phase <> 'DONE'" in sql
    assert s._execute.call_args[0][1][1] == "V1"


@pytest.mark.asyncio
async def test_claim_muralist_batch():
    s = FakeState()
    s._fetch_all.return_value = [{"id": "V1", "title": "t", "has_video": False}]
    vids = await s.claim_muralist_batch(5)
    s._fetch_all.assert_called_once()
    assert vids[0].id == "V1"


@pytest.mark.asyncio
async def test_mark_video_safe():
    s = FakeState()
    await s.mark_video_safe("V1")
    s._execute.assert_called_once()
    sql = s._execute.call_args[0][0]
    assert "has_video = TRUE" in sql
    # Idempotent: clip already DONE → no-op.
    assert "clip_phase <> 'DONE'" in sql
    assert s._execute.call_args[0][1][1] == "V1"


@pytest.mark.asyncio
async def test_mark_fetched():
    s = FakeState()
    await s.mark_fetched("V1", "raw/V1.mp4")
    s._execute.assert_called_once()
    sql, params = s._execute.call_args[0]
    assert "fetched = TRUE" in sql
    assert "raw_uri = %s" in sql
    assert "raw_stored_at = %s" in sql
    # Idempotent: already-fetched raw is never re-marked.
    assert "raw_phase <> 'DONE'" in sql
    assert "captions_uri" not in sql
    assert "has_captions" not in sql
    assert params[0] == "raw/V1.mp4"
    assert params[3] == "V1"


@pytest.mark.asyncio
async def test_reclaim_raw_if_complete_deletes_when_both_done():
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "audio_phase": "DONE",
        "visuals_phase": "DONE",
        "clip_phase": "DONE",
        "raw_stored_at": datetime.now(UTC),
    }
    with (
        patch("atlas.vault.get_vault") as mock_gv,
        patch("atlas.vault.meta_path", return_value="meta/V1.info.json"),
    ):
        mock_vault = mock_gv.return_value
        mock_vault.delete_files = MagicMock(return_value=1)

        deleted = await s.reclaim_raw_if_complete("V1")

    assert deleted == 1
    mock_vault.delete_files.assert_called_once_with(["raw/V1.mp4", "meta/V1.info.json"])
    # Pointer cleared so we never reclaim twice.
    s._execute.assert_called_once()
    assert s._execute.call_args[0][0].startswith("UPDATE videos SET raw_uri = NULL")


@pytest.mark.asyncio
async def test_reclaim_raw_if_complete_skips_when_incomplete():
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "audio_phase": "DONE",
        "visuals_phase": "PENDING",  # painter hasn't derived frames yet → join not met
        "clip_phase": "PENDING",
    }
    deleted = await s.reclaim_raw_if_complete("V1")
    assert deleted == 0
    s._execute.assert_not_called()


@pytest.mark.asyncio
async def test_reclaim_raw_keeps_raw_within_ttl_when_no_clip():
    """Muralist hasn't run (clip not DONE) and raw is fresh → keep raw."""
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "audio_phase": "DONE",
        "visuals_phase": "DONE",
        "clip_phase": "PENDING",
        "raw_stored_at": datetime.now(UTC) - timedelta(hours=10),  # < 48h TTL
    }
    deleted = await s.reclaim_raw_if_complete("V1")
    assert deleted == 0
    s._execute.assert_not_called()


@pytest.mark.asyncio
async def test_reclaim_raw_reclaims_after_ttl_when_no_clip():
    """Muralist never ran but raw aged past TTL → reclaim to bound disk."""
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "audio_phase": "DONE",
        "visuals_phase": "DONE",
        "clip_phase": "PENDING",
        "raw_stored_at": datetime.now(UTC) - timedelta(hours=100),  # > 48h TTL
    }
    with (
        patch("atlas.vault.get_vault") as mock_gv,
        patch("atlas.vault.meta_path", return_value="meta/V1.info.json"),
    ):
        mock_vault = mock_gv.return_value
        mock_vault.delete_files = MagicMock(return_value=1)
        deleted = await s.reclaim_raw_if_complete("V1")
    assert deleted == 1
    mock_vault.delete_files.assert_called_once_with(["raw/V1.mp4", "meta/V1.info.json"])
    s._execute.assert_called_once()
    assert s._execute.call_args[0][0].startswith("UPDATE videos SET raw_uri = NULL")


@pytest.mark.asyncio
async def test_reclaim_raw_keeps_when_stored_at_null_and_no_clip():
    """Rows predating raw_stored_at are never reclaimed via age."""
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "audio_phase": "DONE",
        "visuals_phase": "DONE",
        "clip_phase": "PENDING",
        "raw_stored_at": None,
    }
    deleted = await s.reclaim_raw_if_complete("V1")
    assert deleted == 0
    s._execute.assert_not_called()


@pytest.mark.asyncio
async def test_reclaim_raw_join_barrier_requires_both_mandatory_steps():
    """The join barrier is over phase columns, not booleans. Missing painter
    (visuals not DONE) must block reclamation even if audio is done."""
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "audio_phase": "DONE",
        "visuals_phase": "PROCESSING",  # in-flight, not joined
        "clip_phase": "DONE",
        "raw_stored_at": datetime.now(UTC) - timedelta(hours=200),  # way past TTL
    }
    deleted = await s.reclaim_raw_if_complete("V1")
    assert deleted == 0
    s._execute.assert_not_called()


@pytest.mark.asyncio
async def test_claim_singer_sets_audio_processing_phase():
    s = FakeState()
    s._fetch_all.return_value = []
    await s.claim_singer_batch(5)
    sql = s._fetch_all.call_args[0][0]
    assert "audio_phase = 'PROCESSING'" in sql
    assert "fetched = TRUE" in sql
    assert "has_audio = FALSE" in sql


@pytest.mark.asyncio
async def test_begin_step_and_mark_step_phase():
    s = FakeState()
    await s.begin_step("V1", "visuals")
    sql, params = s._execute.call_args[0]
    assert "visuals_phase = %s::step_phase" in sql
    assert params[0] == "PROCESSING"
    assert params[2] == "V1"

    s._execute.reset_mock()
    await s.mark_step_phase("V1", "clip", "FAILED")
    sql, params = s._execute.call_args[0]
    assert "clip_phase = %s::step_phase" in sql
    assert params[0] == "FAILED"
    # Idempotent: a DONE step is never downgraded.
    assert "clip_phase <> 'DONE'" in sql


@pytest.mark.asyncio
async def test_mark_step_phase_rejects_unknown_step():
    s = FakeState()
    with pytest.raises(ValueError):
        await s.mark_step_phase("V1", "bogus", "DONE")


@pytest.mark.asyncio
async def test_get_pipeline_phase_selects_frontier():
    s = FakeState()
    s._fetch_one.return_value = {"pipeline_phase": "VISUALS"}
    assert await s.get_pipeline_phase("V1") == "VISUALS"
    assert "pipeline_phase" in s._fetch_one.call_args[0][0]


@pytest.mark.asyncio
async def test_claim_scribe_batch_does_not_require_audio():
    """Scribe gets captions from YouTube directly; audio STT is only a paid
    fallback. It must NOT be gated on the singer's audio being present."""
    s = FakeState()
    s._fetch_all.return_value = []
    await s.claim_scribe_batch(5)
    sql = s._fetch_all.call_args[0][0]
    assert "has_audio = TRUE" not in sql
    assert "has_transcript = FALSE" in sql


@pytest.mark.asyncio
async def test_claim_muralist_batch_requires_raw_uri():
    """Muralist needs the raw input — never claim a row whose raw was reclaimed."""
    s = FakeState()
    s._fetch_all.return_value = []
    await s.claim_muralist_batch(5)
    sql = s._fetch_all.call_args[0][0]
    assert "raw_uri IS NOT NULL" in sql
    assert "has_video = FALSE" in sql
