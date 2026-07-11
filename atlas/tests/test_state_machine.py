"""Tests for the VideoStateMixin streamer/singer claim + mark methods."""

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
    assert s._execute.call_args[0][1][1] == "V1"


@pytest.mark.asyncio
async def test_claim_muralist_batch():
    s = FakeState()
    s._fetch_all.return_value = [
        {"id": "V1", "title": "t", "has_video": False}
    ]
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
    assert s._execute.call_args[0][1][1] == "V1"


@pytest.mark.asyncio
async def test_mark_fetched():
    s = FakeState()
    await s.mark_fetched("V1", "raw/V1.mp4")
    s._execute.assert_called_once()
    sql, params = s._execute.call_args[0]
    assert "fetched = TRUE" in sql
    assert "raw_uri = %s" in sql
    assert "captions_uri" not in sql
    assert "has_captions" not in sql
    assert params[0] == "raw/V1.mp4"
    assert params[2] == "V1"


@pytest.mark.asyncio
async def test_reclaim_raw_if_complete_deletes_when_both_done():
    s = FakeState()
    s._fetch_one.return_value = {
        "raw_uri": "raw/V1.mp4",
        "has_audio": True,
        "has_visuals": True,
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
        "has_audio": True,
        "has_visuals": False,  # painter hasn't derived frames yet
    }
    deleted = await s.reclaim_raw_if_complete("V1")
    assert deleted == 0
    s._execute.assert_not_called()
