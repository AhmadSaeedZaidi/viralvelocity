"""Tests for the Janitor archival vault-safety gates.

Regression for a rediscovered bug: janitor archival deleted transcript rows and
zeroed ``has_*`` flags with **no check** that the content ever reached the vault,
so (a) unflushed transcripts were silently destroyed and (b) the heartbeat's hot
``transcripts``/``with_visuals``/``audios`` counts shrank every cycle.

The fix — assert these in the SQL the repository issues:
  1. ``sweep_archivable`` / ``count_archivable`` exclude any video whose
     transcript isn't confirmed in the vault (unflushable, or ``vault_uri IS NULL``).
  2. ``archive_video_batch`` refuses to purge a video whose transcript still
     lacks a ``vault_uri``.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from atlas.config import settings
from atlas.models import Video
from atlas.repositories.video.janitor import VideoJanitorMixin


class FakeJanitor(VideoJanitorMixin):
    """VideoJanitorMixin with the low-level DB helpers stubbed out."""

    def __init__(self) -> None:
        self._fetch_all = AsyncMock()
        self._fetch_one = AsyncMock()
        self._execute = AsyncMock()


def _make_video(video_id: str) -> Video:
    return Video(
        id=video_id,
        channel_id="ch",
        title="t",
        status="PROCESSED",
        last_updated_at=datetime.now(UTC) - timedelta(days=30),
    )


@pytest.mark.asyncio
async def test_sweep_excludes_unvaulted_transcript():
    """A processed video whose transcript was never written must not archive."""
    s = FakeJanitor()
    s._fetch_all.return_value = []
    await s.sweep_archivable(10)
    sql = s._fetch_all.call_args[0][0]
    assert "vault_write_pending IS NOT TRUE" in sql
    assert "t.vault_uri IS NULL" in sql


@pytest.mark.asyncio
async def test_count_archivable_applies_gate():
    s = FakeJanitor()
    s._fetch_one.return_value = {"total": 0}
    await s.count_archivable()
    sql = s._fetch_one.call_args[0][0]
    assert "vault_write_pending IS NOT TRUE" in sql
    assert "t.vault_uri IS NULL" in sql


@pytest.mark.asyncio
async def test_archive_skips_unvaulted_transcript():
    """Even if sweep were bypassed, archive must not purge a vault-pending row.

    The vault safety check raises inside the per-video loop; archive_video_batch
    catches that as a per-video failure and leaves the row (no write transaction),
    rather than silently deleting an unflushed transcript.
    """
    s = FakeJanitor()
    s.get_latest_stats_batch = AsyncMock(return_value={})
    # archive_video_batch calls get_latest_stats_batch (a no-op here) and then the
    # per-video vault safety check; the safety check is the last _fetch_one call.
    s._fetch_one.return_value = {"unvaulted": True}
    with patch("atlas.vault.get_vault") as mock_vault:
        mock_vault.return_value.store_batch.return_value = []
        with patch.object(settings, "JANITOR_SAFETY_CHECK", False):
            result = await s.archive_video_batch([_make_video("V1")])
    # The video was recorded as failed / not archived, and no write executed.
    assert result["failed"] == 1
    assert result["archived"] == 0
    assert not s._execute.called
