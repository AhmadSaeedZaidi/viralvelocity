# Ghost Tracking

**Infinite video tracking with minimal SQL footprint**

Ghost Tracking decouples video metadata (ephemeral, cleaned after 7 days) from tracking schedules (persistent) and metrics data (Parquet in Vault), enabling:
- Track videos **forever**
- Store unlimited time-series data in Vault
- Maintain <0.5 GB SQL footprint
- Adaptive tracking frequency based on video age

---

## Overview

### The Problem

Traditional approaches store all video data and metrics in SQL:
- ❌ Metrics accumulate quickly (100s of bytes per point)
- ❌ Tracking stops when videos are deleted (retention policies)
- ❌ SQL database grows unbounded
- ❌ Fixed tracking frequency for all videos

### The Solution

**Ghost Tracking** separates concerns:

```
┌─────────────────────────────────────┐
│   HOT QUEUE (PostgreSQL <0.5GB)    │
│                                     │
│  videos: Deleted after 7 days      │  ← Ephemeral metadata
│  watchlist: Persists forever       │  ← Lightweight schedule
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   COLD VAULT (Parquet Files)       │
│   Unlimited time-series storage     │  ← Heavy metrics data
└─────────────────────────────────────┘
```

---

## Architecture

### Components

#### 1. Watchlist Table (SQL)

Lightweight tracking schedule that persists forever:

```sql
CREATE TABLE watchlist (
    video_id VARCHAR(20) PRIMARY KEY,
    tracking_tier VARCHAR(20) DEFAULT 'HOURLY',
    last_tracked_at TIMESTAMP,
    next_track_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Size**: ~50 bytes per video (minimal footprint)

#### 2. Videos Table (SQL)

Ephemeral metadata for processing (deleted after 7 days):

```sql
CREATE TABLE videos (
    id VARCHAR(20) PRIMARY KEY,
    title TEXT,
    published_at TIMESTAMP,
    tags TEXT[],
    -- ... rich metadata for analysis
);
```

**Lifecycle**: Cleaned by Janitor after 7 days

#### 3. Metrics Files (Vault/Parquet)

Time-series data stored in Hive-partitioned Parquet files:

```
vault/
└── metrics/
    └── date=2026-01-15/
        └── hour=10/
            └── stats.parquet
                ├── video_id
                ├── timestamp
                ├── views
                ├── likes
                ├── comments
                └── published_at
```

**Size**: Unlimited, compressed, columnar storage

---

## Data Flow

### 1. Discovery (Hunter)

```python
# Hunter finds new video
await dao.ingest_video_metadata(video_data)  # → videos table
await dao.add_to_watchlist(video_id)          # → watchlist table
```

### 2. Tracking (Tracker)

```python
# Fetch from watchlist (not videos table!)
batch = await dao.fetch_tracking_batch(50)

# Query YouTube API
stats = await youtube_api.get_statistics(video_ids)

# Store to Vault (not SQL!)
vault.append_metrics(metrics_data)

# Update schedule based on age
tier, next_time = dao.calculate_next_track_time(published_at)
await dao.update_watchlist_schedule(updates)
```

### 3. Cleanup (Janitor)

```python
# After 7 days, delete video metadata
await dao.run_janitor()  # Deletes from videos table

# Watchlist remains!
# Tracking continues indefinitely
```

---

## Tracking Tiers

Videos are tracked at adaptive frequencies based on age:

| Age | Tier | Frequency | Interval | Rationale |
|-----|------|-----------|----------|-----------|
| < 24h | HOURLY | Every hour | +1 hour | Catch viral spikes |
| 1-7 days | DAILY | Every day | +24 hours | Monitor sustained growth |
| > 7 days | WEEKLY | Every week | +7 days | Long-term tracking |

**Automatic adjustment**: Tier changes as video ages.

### Example Timeline

```
Day 0 (00:00): Video discovered → HOURLY tier
Day 0 (01:00): Track #1 → Still HOURLY
Day 0 (23:00): Track #23 → Still HOURLY
Day 1 (00:00): Track #24 → Switches to DAILY
Day 2 (00:00): Track #25 → DAILY
Day 7 (00:00): Track #30 → Switches to WEEKLY
Day 7 (12:00): Janitor deletes video row
Day 14 (00:00): Track #31 → Still works! (Ghost Tracking)
```

---

## Implementation

### DAO Methods

```python
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()

# Add video to watchlist
await dao.add_to_watchlist("VIDEO_123", tier="HOURLY")

# Fetch videos needing updates
batch = await dao.fetch_tracking_batch(batch_size=50)
# Returns: [{'video_id': 'VIDEO_123', 'tracking_tier': 'HOURLY', ...}]

# Calculate next schedule
tier, next_track_at = dao.calculate_next_track_time(
    published_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
)

# Update schedules
updates = [{
    'video_id': 'VIDEO_123',
    'tracking_tier': 'DAILY',
    'last_tracked_at': datetime.now(timezone.utc),
    'next_track_at': datetime.now(timezone.utc) + timedelta(days=1)
}]
await dao.update_watchlist_schedule(updates)
```

### Vault Methods

```python
from atlas.vault import vault

# Append time-series metrics
metrics = [{
    'video_id': 'VIDEO_123',
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'views': 1000,
    'likes': 50,
    'comments': 10,
    'published_at': '2026-01-15T10:00:00Z'
}]

# Automatically partitioned by date/hour
vault.append_metrics(metrics)
# Writes to: metrics/date=2026-01-15/hour=10/stats.parquet
```

---

## Benefits

### Space Efficiency

**Before Ghost Tracking:**
- All metrics in SQL
- ~100 bytes per metric point
- 1 video tracked hourly for 1 year = 876,000 bytes
- 100k videos = 87.6 GB

**After Ghost Tracking:**
- Only schedule in SQL (~50 bytes per video)
- Metrics in compressed Parquet (~20 bytes per point)
- 100k videos watchlist = 5 MB in SQL
- Metrics = unlimited in Vault

### Tracking Duration

**Before**: Limited by SQL retention (7 days)  
**After**: Unlimited (years)

### Performance

- Watchlist queries use `next_track_at` index (fast)
- Parquet queries use columnar format (efficient)
- No slow sequential scans on large tables

---

## Query Patterns

### Find Videos Needing Updates

```sql
SELECT video_id, tracking_tier, next_track_at
FROM watchlist
WHERE next_track_at <= NOW()
ORDER BY next_track_at ASC
LIMIT 50
FOR UPDATE SKIP LOCKED;
```

### Check Tracking Status

```sql
-- Videos by tier
SELECT tracking_tier, COUNT(*) 
FROM watchlist 
GROUP BY tracking_tier;

-- Next 10 videos to track
SELECT video_id, tracking_tier, next_track_at
FROM watchlist
WHERE next_track_at <= NOW() + INTERVAL '1 hour'
ORDER BY next_track_at ASC
LIMIT 10;
```

### Read Time-Series from Vault

```python
import pandas as pd

# Read specific date/hour
df = pd.read_parquet('vault/metrics/date=2026-01-15/hour=10/stats.parquet')

# Read date range
df = pd.read_parquet(
    'vault/metrics/',
    filters=[('date', '>=', '2026-01-01'), ('date', '<=', '2026-01-15')]
)

# Aggregate views per video
views_by_video = df.groupby('video_id')['views'].agg(['min', 'max', 'mean'])
```

---

## Best Practices

### 1. Always Add to Watchlist

When Hunter discovers a video:

```python
# ✅ GOOD
await dao.ingest_video_metadata(video_data)
await dao.add_to_watchlist(video_id, tier="HOURLY")

# ❌ BAD
await dao.ingest_video_metadata(video_data)
# Missing watchlist entry - tracking will stop after cleanup
```

### 2. Never Update videos Table Stats

Tracker should only write to Vault:

```python
# ✅ GOOD
vault.append_metrics(metrics_data)
await dao.update_watchlist_schedule(updates)

# ❌ BAD
await dao.update_video_stats_batch(updates)  # Row may not exist!
```

### 3. Handle Missing Videos Gracefully

```python
# Video may have been deleted by Janitor
if published_at is None:
    logger.warning(f"Video {video_id} has no publishedAt, skipping")
    continue
```

### 4. Batch Watchlist Updates

```python
# ✅ GOOD - Single batch update
updates = [calculate_update(v) for v in batch]
await dao.update_watchlist_schedule(updates)

# ❌ BAD - Multiple individual updates
for video in batch:
    await dao.update_watchlist_schedule([video])  # N queries!
```

---

## Monitoring

### Watchlist Health

```sql
-- Total videos tracked
SELECT COUNT(*) FROM watchlist;

-- Distribution by tier
SELECT tracking_tier, COUNT(*) 
FROM watchlist 
GROUP BY tracking_tier;

-- Backlog (videos overdue)
SELECT COUNT(*) 
FROM watchlist 
WHERE next_track_at < NOW() - INTERVAL '1 hour';
```

### Vault Health

```bash
# Check partition sizes
du -sh vault/metrics/date=*/

# Count total metric points
python << EOF
import pandas as pd
df = pd.read_parquet('vault/metrics/')
print(f"Total metrics: {len(df)}")
print(f"Unique videos: {df['video_id'].nunique()}")
EOF
```

---

## Troubleshooting

### Watchlist Not Growing

**Symptom**: Watchlist count stays at 0  
**Cause**: Hunter not adding videos to watchlist  
**Fix**: Ensure `dao.add_to_watchlist()` is called after ingestion

### Metrics Not Appearing in Vault

**Symptom**: Vault directory empty  
**Cause**: Vault credentials or Tracker not running  
**Fix**: Check `vault.health_check()` and Tracker logs

### Videos Tracked Too Frequently

**Symptom**: Too many API calls  
**Cause**: Tier not updating based on age  
**Fix**: Verify `calculate_next_track_time()` logic

---

## Migration

### From Old Tracker to Ghost Tracking

```sql
-- 1. Create watchlist from existing videos
INSERT INTO watchlist (video_id, tracking_tier, last_tracked_at, next_track_at)
SELECT 
    id as video_id,
    'DAILY' as tracking_tier,
    last_updated_at as last_tracked_at,
    NOW() as next_track_at
FROM videos
ON CONFLICT (video_id) DO NOTHING;

-- 2. Verify
SELECT COUNT(*) FROM watchlist;
```

---

## Summary

**Ghost Tracking** enables infinite video tracking while maintaining SQL efficiency:

- ✅ Track videos forever (even after deletion)
- ✅ Unlimited metrics in Parquet (compressed, columnar)
- ✅ <0.5 GB SQL footprint
- ✅ Adaptive tracking frequency
- ✅ Decoupled from hot queue retention

**Result**: Track 100k+ videos/day indefinitely within SQL constraints.
