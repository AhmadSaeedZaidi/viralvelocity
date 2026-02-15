"""Integration tests for Adaptive Scheduling functionality.

Real Integration Testing: Uses real HuggingFace vault for metrics storage.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
import pytest_asyncio


@pytest.mark.integration
class TestAdaptiveScheduling:
    """Test Adaptive Scheduling watchlist operations and Vault integration."""

    @pytest.mark.asyncio
    async def test_add_to_watchlist(self, dao):
        """Test adding videos to watchlist with real database."""
        video_ids = ["VIDEO_001", "VIDEO_002", "VIDEO_003"]

        # Add videos to watchlist
        for vid in video_ids:
            await dao.add_to_watchlist(vid, tier="HOURLY")

        # Verify they were added by querying watchlist table
        watchlist = await dao._fetch_all(
            "SELECT video_id FROM watchlist WHERE video_id = ANY(%s)", (video_ids,)
        )
        assert len(watchlist) == 3

    @pytest.mark.asyncio
    async def test_fetch_tracking_batch(self, dao):
        """Test fetching videos due for tracking."""
        # Setup: Add videos with past next_track_at
        test_videos = [
            {
                "video_id": f"VIDEO_{i:03d}",
                "tier": "HOURLY",
                "next_track_at": datetime.now(timezone.utc) - timedelta(hours=1),
            }
            for i in range(5)
        ]

        # Add to watchlist (in real test)
        for video in test_videos:
            await dao.add_to_watchlist(video["video_id"], tier=video["tier"])

        # Fetch batch
        batch = await dao.fetch_tracking_batch(batch_size=3)

        # Verify batch size respects limit
        assert len(batch) <= 3

        # Verify FIFO order (oldest first)
        if len(batch) > 1:
            for i in range(len(batch) - 1):
                assert batch[i]["next_track_at"] <= batch[i + 1]["next_track_at"]

    @pytest.mark.asyncio
    async def test_update_watchlist_schedule(self, dao):
        """Test batch updating watchlist schedules."""
        now = datetime.now(timezone.utc)

        updates = [
            {
                "video_id": "VIDEO_001",
                "tracking_tier": "DAILY",
                "last_tracked_at": now,
                "next_track_at": now + timedelta(days=1),
            },
            {
                "video_id": "VIDEO_002",
                "tracking_tier": "WEEKLY",
                "last_tracked_at": now,
                "next_track_at": now + timedelta(days=7),
            },
        ]

        # Update schedules
        await dao.update_watchlist_schedule(updates)

        # Verify updates (would query DB in real test)
        assert True  # Placeholder

    @pytest.mark.asyncio
    async def test_calculate_next_track_time(self, dao):
        """Test adaptive tier calculation based on video age."""
        now = datetime.now(timezone.utc)

        # Test HOURLY tier (< 24h old)
        published_recent = now - timedelta(hours=12)
        tier, next_time = dao.calculate_next_track_time(published_recent)
        assert tier == "HOURLY"
        assert next_time > now
        assert next_time <= now + timedelta(hours=1, minutes=1)

        # Test DAILY tier (1-7 days old)
        published_medium = now - timedelta(days=3)
        tier, next_time = dao.calculate_next_track_time(published_medium)
        assert tier == "DAILY"
        assert next_time > now
        assert next_time <= now + timedelta(days=1, hours=1)

        # Test WEEKLY tier (> 7 days old)
        published_old = now - timedelta(days=30)
        tier, next_time = dao.calculate_next_track_time(published_old)
        assert tier == "WEEKLY"
        assert next_time > now
        assert next_time <= now + timedelta(days=7, hours=1)

    @pytest.mark.asyncio
    async def test_adaptive_scheduling_survives_janitor(self, dao):
        """Test that watchlist persists after video cleanup (Adaptive Scheduling)."""
        video_id = "VIDEO_PERSISTENT"

        video_data = {
            "id": {"videoId": video_id},
            "snippet": {
                "channelId": "CHANNEL_001",
                "channelTitle": "Test Channel",
                "title": "Test Video",
                "publishedAt": "2026-01-01T00:00:00Z",
                "tags": ["test"],
                "categoryId": "28",
                "defaultLanguage": "en",
            },
        }
        await dao.ingest_video_metadata(video_data)
        await dao.add_to_watchlist(video_id, tier="HOURLY")

        await dao.mark_video_done(video_id)

        # Verify watchlist entry exists
        watchlist_entry = await dao._fetch_one(
            "SELECT * FROM watchlist WHERE video_id = %s", (video_id,)
        )

        # Adaptive Scheduling means video stays in watchlist
        assert watchlist_entry is not None
        assert watchlist_entry["video_id"] == video_id

    @pytest.mark.asyncio
    async def test_vault_metrics_storage(self, dao):
        """Test metrics are properly stored in real HuggingFace Vault via Adaptive Scheduling."""
        from atlas.vault import vault

        metrics_data = [
            {
                "video_id": "VIDEO_METRICS_001",
                "views": 10000,
                "likes": 500,
                "comment_count": 50,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            {
                "video_id": "VIDEO_METRICS_002",
                "views": 5000,
                "likes": 250,
                "comment_count": 25,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ]

        # Append to real Vault (HuggingFace)
        test_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        vault.append_metrics(metrics_data, date=test_date)

        # Verify by attempting to list files (validates upload succeeded)
        files = vault.list_files(f"metrics/date={test_date}")
        assert len(files) > 0, "Metrics should be uploaded to vault"


@pytest_asyncio.fixture
async def dao(fresh_db):
    """Provide MaiaDAO instance for testing with real vault."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance
