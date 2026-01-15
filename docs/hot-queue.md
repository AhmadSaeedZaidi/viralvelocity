# Hot Queue Architecture

**Ephemeral data management for high-throughput ingestion**

The Hot Queue is Pleiades' ephemeral data management system that enables ingestion of 100k+ videos/day while maintaining a <0.5 GB SQL footprint.

---

## Overview

### The Problem

Traditional approaches store all discovered content permanently:
- ❌ Database grows unbounded
- ❌ Queries slow down over time
- ❌ Storage costs increase linearly
- ❌ No clear data lifecycle

### The Solution

**Hot Queue Architecture** implements time-based retention:
- ✅ Videos auto-deleted after 7 days
- ✅ SQL footprint stays constant (<0.5 GB)
- ✅ Fast queries on recent data only
- ✅ Long-term tracking via Ghost Tracking

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    HOT QUEUE (SQL)                         │
│                     <0.5 GB Target                         │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  search_queue (Discovery Coordination)                    │
│  ┌──────────────────────────────────────┐                │
│  │ query_term, next_page_token, ...     │                │
│  │ Persistent (never deleted)           │                │
│  └──────────────────────────────────────┘                │
│                                                            │
│  videos (Ephemeral Metadata)                              │
│  ┌──────────────────────────────────────┐                │
│  │ id, title, published_at, tags...     │                │
│  │ Deleted after 7 days ✂️              │                │
│  └──────────────────────────────────────┘                │
│                                                            │
│  watchlist (Tracking Schedule)                            │
│  ┌──────────────────────────────────────┐                │
│  │ video_id, tracking_tier, next_track  │                │
│  │ Persistent forever (Ghost Tracking)  │                │
│  └──────────────────────────────────────┘                │
│                                                            │
└────────────────────────────────────────────────────────────┘
                         ↓
┌────────────────────────────────────────────────────────────┐
│                  COLD VAULT (Parquet)                      │
│                   Unlimited Storage                        │
├────────────────────────────────────────────────────────────┤
│  metrics/date=YYYY-MM-DD/hour=HH/stats.parquet            │
│  Long-term time-series data                               │
└────────────────────────────────────────────────────────────┘
```

---

## Tables

### search_queue (Persistent)

Discovery coordination for Hunter:

```sql
CREATE TABLE search_queue (
    id SERIAL PRIMARY KEY,
    query_term VARCHAR(255) UNIQUE NOT NULL,
    next_page_token TEXT,
    last_searched_at TIMESTAMP,
    priority INT DEFAULT 5,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Lifecycle**: Never deleted (coordination state)

**Purpose**: Track which queries to search and pagination state

### videos (Ephemeral - 7 Days)

Rich metadata for processing:

```sql
CREATE TABLE videos (
    id VARCHAR(20) PRIMARY KEY,
    title TEXT NOT NULL,
    channel_id VARCHAR(50),
    channel_title TEXT,
    published_at TIMESTAMP NOT NULL,
    description TEXT,
    tags TEXT[],
    category_id INTEGER,
    duration VARCHAR(20),
    view_count BIGINT,
    like_count BIGINT,
    comment_count BIGINT,
    thumbnail_url TEXT,
    discovered_at TIMESTAMP DEFAULT NOW(),
    last_updated_at TIMESTAMP
);
```

**Lifecycle**: Deleted after 7 days

**Purpose**: Provide rich context for analysis, then cleanup

### watchlist (Persistent - Ghost Tracking)

Lightweight tracking schedule:

```sql
CREATE TABLE watchlist (
    video_id VARCHAR(20) PRIMARY KEY,
    tracking_tier VARCHAR(20) DEFAULT 'HOURLY',
    last_tracked_at TIMESTAMP,
    next_track_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Lifecycle**: Never deleted

**Purpose**: Enable infinite tracking without storing heavy metadata

---

## Data Lifecycle

### Day 0: Discovery

```
Hunter → Discovers video
       → Inserts into videos table
       → Adds to watchlist (Ghost Tracking)
```

### Days 1-7: Processing

```
Tracker → Reads from watchlist
        → Fetches stats from YouTube API
        → Writes metrics to Vault (Parquet)
        → Updates watchlist schedule

Scribe → May read from videos table for analysis
       → Extracts features, generates embeddings
```

### Day 7: Cleanup

```
Janitor → Scans videos table
        → Deletes rows WHERE discovered_at < NOW() - INTERVAL '7 days'
        → Watchlist remains intact
```

### Day 8+: Ghost Tracking

```
Tracker → Still reads from watchlist
        → Video row deleted, but tracking continues!
        → Writes metrics to Vault
```

---

## Janitor Agent

The Janitor enforces the 7-day retention policy:

```python
@flow(name="run_janitor_cycle")
async def run_janitor_cycle():
    """Clean up old videos from hot queue."""
    dao = MaiaDAO()
    
    result = await dao.run_janitor()
    
    logger.info(
        f"Janitor cleaned {result['deleted']} videos "
        f"(cutoff: {result['cutoff_date']})"
    )
```

### DAO Implementation

```python
async def run_janitor(self) -> Dict[str, Any]:
    """Delete videos older than retention period."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(
        days=settings.JANITOR_RETENTION_DAYS
    )
    
    # Safety check: Ensure we're not deleting everything
    if settings.JANITOR_SAFETY_CHECK:
        count = await self._fetch_one(
            "SELECT COUNT(*) as total FROM videos WHERE discovered_at >= %s",
            (cutoff_date,)
        )
        if count["total"] == 0:
            raise Exception("SAFETY: Would delete all videos")
    
    # Delete old videos (watchlist is NOT affected)
    query = """
        DELETE FROM videos 
        WHERE discovered_at < %s
        RETURNING id
    """
    deleted = await self._fetch_all(query, (cutoff_date,))
    
    return {
        "deleted": len(deleted),
        "cutoff_date": cutoff_date.isoformat(),
        "retention_days": settings.JANITOR_RETENTION_DAYS
    }
```

### Configuration

```bash
# Janitor settings
JANITOR_RETENTION_DAYS=7
JANITOR_SAFETY_CHECK=true
```

---

## Benefits

### Space Efficiency

**Without Hot Queue:**
```
Day 1:  10,000 videos → 100 MB
Day 30: 300,000 videos → 3 GB
Day 90: 900,000 videos → 9 GB
```

**With Hot Queue:**
```
Day 1:  10,000 videos → 100 MB
Day 7:  70,000 videos → 700 MB (stable)
Day 30: 70,000 videos → 700 MB (stable)
Day 90: 70,000 videos → 700 MB (stable)
```

### Query Performance

**Without Hot Queue:**
- Sequential scan on 900k rows (slow)
- Index size grows proportionally

**With Hot Queue:**
- Sequential scan on 70k rows only (fast)
- Index size stays constant

### Cost Savings

**Without Hot Queue:**
- SQL storage: $0.23/GB/month × 9 GB = $2.07/month
- Growing linearly

**With Hot Queue:**
- SQL storage: $0.23/GB/month × 0.7 GB = $0.16/month
- Constant over time

---

## Safety Measures

### 1. Safety Check (Default: Enabled)

Prevents accidental deletion of all videos:

```python
if settings.JANITOR_SAFETY_CHECK:
    # Ensure we're not deleting everything
    recent_count = await dao.count_recent_videos()
    if recent_count == 0:
        raise Exception("SAFETY: Would delete all videos")
```

### 2. Dry Run Mode

```python
# Preview what would be deleted
result = await dao.preview_janitor()
logger.info(f"Would delete {result['would_delete']} videos")
```

### 3. Watchlist Protection

The Janitor explicitly excludes the watchlist table:

```python
# ✅ Deletes from videos only
DELETE FROM videos WHERE discovered_at < cutoff;

# ❌ Never deletes from watchlist
# (Watchlist is for Ghost Tracking - persists forever)
```

---

## Monitoring

### Hot Queue Health

```sql
-- Current hot queue size
SELECT 
    COUNT(*) as video_count,
    pg_size_pretty(pg_total_relation_size('videos')) as table_size
FROM videos;

-- Age distribution
SELECT 
    EXTRACT(day FROM NOW() - discovered_at) as age_days,
    COUNT(*) as count
FROM videos
GROUP BY age_days
ORDER BY age_days;

-- Videos approaching deletion
SELECT COUNT(*) as approaching_deletion
FROM videos
WHERE discovered_at < NOW() - INTERVAL '6 days';
```

### Janitor Metrics

```python
# Log cleanup results
logger.info(
    f"Janitor cycle: "
    f"deleted={result['deleted']}, "
    f"cutoff={result['cutoff_date']}, "
    f"retention_days={result['retention_days']}"
)

# Alert on anomalies
if result['deleted'] > 100000:
    await notifier.send(
        level="warning",
        message=f"Janitor deleted {result['deleted']} videos (unusually high)"
    )
```

---

## Best Practices

### 1. Set Appropriate Retention

```bash
# Development: 1 day
JANITOR_RETENTION_DAYS=1

# Production: 7 days (recommended)
JANITOR_RETENTION_DAYS=7

# Long retention: 30 days (higher SQL cost)
JANITOR_RETENTION_DAYS=30
```

### 2. Run Janitor Regularly

```python
# Cron schedule: Daily at 2 AM
0 2 * * * python -m maia.janitor.flow

# Or: Continuous loop with sleep
while True:
    await run_janitor_cycle()
    await asyncio.sleep(24 * 3600)  # 24 hours
```

### 3. Never Disable Safety Check in Production

```bash
# Development: Can disable
JANITOR_SAFETY_CHECK=false

# Production: Always enable
JANITOR_SAFETY_CHECK=true
```

### 4. Combine with Ghost Tracking

```python
# When ingesting videos
await dao.ingest_video_metadata(video_data)  # → videos (ephemeral)
await dao.add_to_watchlist(video_id)          # → watchlist (persistent)

# Tracking continues even after Janitor cleanup
```

---

## Migration

### Enable Hot Queue on Existing Database

```sql
-- 1. Add discovered_at to existing videos
ALTER TABLE videos 
ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMP DEFAULT NOW();

-- 2. Backfill for existing rows
UPDATE videos 
SET discovered_at = created_at 
WHERE discovered_at IS NULL;

-- 3. Run Janitor manually
-- (Or wait for scheduled run)
```

### Disable Hot Queue (Revert to Permanent Storage)

```bash
# Set very long retention
JANITOR_RETENTION_DAYS=36500  # 100 years
```

---

## Troubleshooting

### Videos Deleted Too Quickly

**Symptom**: Videos disappear before processing  
**Cause**: Retention too short  
**Fix**: Increase `JANITOR_RETENTION_DAYS`

### SQL Still Growing

**Symptom**: Database size keeps increasing  
**Cause**: Janitor not running or watchlist growing  
**Fix**: 
- Verify Janitor is scheduled
- Check watchlist size (should be small)

### All Videos Deleted

**Symptom**: Videos table empty  
**Cause**: Safety check disabled or clock skew  
**Fix**:
- Enable `JANITOR_SAFETY_CHECK=true`
- Verify system clock

---

## Summary

**Hot Queue Architecture** enables high-throughput ingestion:

- ✅ Constant SQL footprint (<0.5 GB)
- ✅ Fast queries on recent data
- ✅ Automatic cleanup after 7 days
- ✅ Infinite tracking via Ghost Tracking
- ✅ Safety measures to prevent accidents

**Result**: Ingest 100k+ videos/day indefinitely within SQL constraints.
