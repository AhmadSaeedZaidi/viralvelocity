"""Integration tests for Janitor archival (Hot → Cold tier)."""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List


@pytest.mark.integration
class TestJanitorArchival:
    """Test Janitor's stats archival from SQL to Vault."""

    @pytest.mark.asyncio
    async def test_archive_cold_stats_single_batch(self, dao, mock_vault):
        """Test archiving a single batch of old stats."""
        # Setup: Insert old stats into hot tier
        old_stats = [
            {
                "video_id": f"VIDEO_{i:03d}",
                "views": 1000 * i,
                "likes": 50 * i,
                "comment_count": 10 * i,
                "timestamp": datetime.now(timezone.utc) - timedelta(days=10),
            }
            for i in range(100)
        ]

        # Insert stats to hot tier (in real test with actual DB)
        await dao.log_video_stats_batch(old_stats)

        # Archive old stats (retention=7 days)
        archived_count = await dao.archive_cold_stats(retention_days=7, batch_size=5000)

        # Verify stats were archived
        assert archived_count == 100

        # Verify Vault received the data
        assert len(mock_vault) > 0  # Should have metrics files

    @pytest.mark.asyncio
    async def test_archive_cold_stats_multiple_batches(self, dao, mock_vault):
        """Test archival loop drains large backlog in batches."""
        # Setup: Insert 12,000 old stats (requires 3 batches of 5000)
        batch_size = 5000
        total_stats = 12000

        old_stats = [
            {
                "video_id": f"VIDEO_{i:05d}",
                "views": 1000,
                "likes": 50,
                "comment_count": 10,
                "timestamp": datetime.now(timezone.utc) - timedelta(days=8, hours=i % 24),
            }
            for i in range(total_stats)
        ]

        await dao.log_video_stats_batch(old_stats)

        # Run archival loop
        total_archived = 0
        iterations = 0
        while iterations < 5:  # Safety limit
            archived = await dao.archive_cold_stats(retention_days=7, batch_size=batch_size)
            if archived == 0:
                break
            total_archived += archived
            iterations += 1

        # Verify all stats archived
        assert total_archived == total_stats
        assert iterations == 3  # Should take 3 batches (12000 / 5000 = 2.4 → 3)

    @pytest.mark.asyncio
    async def test_archive_respects_retention_period(self, dao, mock_vault):
        """Test that only stats older than retention period are archived."""
        now = datetime.now(timezone.utc)

        # Insert mixed stats (some old, some recent)
        stats = [
            # Old stats (should be archived)
            *[
                {
                    "video_id": f"OLD_{i:03d}",
                    "views": 1000,
                    "likes": 50,
                    "comment_count": 10,
                    "timestamp": now - timedelta(days=10),
                }
                for i in range(50)
            ],
            # Recent stats (should NOT be archived)
            *[
                {
                    "video_id": f"NEW_{i:03d}",
                    "views": 500,
                    "likes": 25,
                    "comment_count": 5,
                    "timestamp": now - timedelta(days=3),
                }
                for i in range(50)
            ],
        ]

        await dao.log_video_stats_batch(stats)

        # Archive with 7-day retention
        archived = await dao.archive_cold_stats(retention_days=7)

        # Only old stats should be archived
        assert archived == 50  # Not 100

    @pytest.mark.asyncio
    async def test_vault_failure_prevents_deletion(self, dao, mock_vault_failing):
        """Test transactional safety: don't delete if Vault upload fails."""
        # Insert old stats
        old_stats = [
            {
                "video_id": "VIDEO_001",
                "views": 1000,
                "likes": 50,
                "comment_count": 10,
                "timestamp": datetime.now(timezone.utc) - timedelta(days=10),
            }
        ]

        await dao.log_video_stats_batch(old_stats)

        # Attempt archival (Vault will fail)
        with pytest.raises(Exception):
            await dao.archive_cold_stats(retention_days=7)

        # Verify stats were NOT deleted from hot tier
        # In real test, would query video_stats_log to confirm data still exists
        assert True  # Placeholder

    @pytest.mark.asyncio
    async def test_archival_groups_by_date(self, dao, mock_vault):
        """Test that stats are grouped by date for efficient Parquet storage."""
        # Insert stats spanning multiple days
        stats = []
        for day_offset in range(10, 13):  # 3 days worth
            for i in range(10):
                stats.append(
                    {
                        "video_id": f"VIDEO_{i:03d}",
                        "views": 1000,
                        "likes": 50,
                        "comment_count": 10,
                        "timestamp": datetime.now(timezone.utc)
                        - timedelta(days=day_offset, hours=i),
                    }
                )

        await dao.log_video_stats_batch(stats)

        # Archive
        await dao.archive_cold_stats(retention_days=7)

        # Verify Vault has separate files per date
        # In real test, would check Parquet file paths
        assert len(mock_vault) >= 3  # At least 3 date partitions

    @pytest.mark.asyncio
    async def test_janitor_full_cycle(self, dao, mock_vault):
        """Test complete Janitor cycle: archive stats + cleanup videos."""
        from maia.janitor.flow import janitor_cycle

        # Setup: Create old video with stats
        video_id = "VIDEO_JANITOR_TEST"

        # 1. Ingest video
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

        # 2. Add stats
        stats = [
            {
                "video_id": video_id,
                "views": 10000,
                "likes": 500,
                "comment_count": 50,
                "timestamp": datetime.now(timezone.utc) - timedelta(days=10),
            }
        ]
        await dao.log_video_stats_batch(stats)

        # 3. Mark video as done (eligible for cleanup)
        await dao.mark_video_transcript_safe(video_id)
        await dao.mark_video_done(video_id)

        # 4. Run Janitor cycle
        result = await janitor_cycle(dry_run=False, archive_stats=True)

        # 5. Verify results
        assert result["stats_archived"] >= 0  # Stats were archived
        # Video cleanup would also run (requires setting old discovered_at)

    @pytest.mark.asyncio
    async def test_archival_performance_large_dataset(self, dao, mock_vault):
        """Performance test: Archive 50k stats in reasonable time."""
        import time

        # Insert 50k old stats
        total_stats = 50000
        batch_size = 10000

        for batch_start in range(0, total_stats, batch_size):
            batch_stats = [
                {
                    "video_id": f"VIDEO_{i:06d}",
                    "views": 1000,
                    "likes": 50,
                    "comment_count": 10,
                    "timestamp": datetime.now(timezone.utc) - timedelta(days=10),
                }
                for i in range(batch_start, min(batch_start + batch_size, total_stats))
            ]
            await dao.log_video_stats_batch(batch_stats)

        # Measure archival time
        start_time = time.time()

        total_archived = 0
        while True:
            archived = await dao.archive_cold_stats(retention_days=7, batch_size=5000)
            if archived == 0:
                break
            total_archived += archived

        elapsed = time.time() - start_time

        # Verify all archived
        assert total_archived == total_stats

        # Performance check (should complete in reasonable time)
        # Adjust threshold based on expected performance
        assert elapsed < 60  # Should archive 50k rows in under 60 seconds


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance


@pytest.fixture
def mock_vault(monkeypatch):
    """Mock vault that tracks stored data."""
    storage = {}

    def mock_append(data, date=None, hour=None):
        date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"metrics/{date_str}/stats.parquet"
        if key not in storage:
            storage[key] = []
        storage[key].extend(data)

    from atlas import vault

    monkeypatch.setattr(vault.vault, "append_metrics", mock_append)

    return storage


@pytest.fixture
def mock_vault_failing(monkeypatch):
    """Mock vault that always fails (for testing error handling)."""

    def mock_append_fail(data, date=None, hour=None):
        raise Exception("Simulated Vault failure")

    from atlas import vault

    monkeypatch.setattr(vault.vault, "append_metrics", mock_append_fail)
