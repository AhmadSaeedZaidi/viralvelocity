# Maia Architecture

## Overview

Maia is the stateless agent layer of Project Pleiades, designed for high-velocity video discovery and viral velocity monitoring. It is built on Prefect and operates as a pure agent - all state and persistence is handled by Atlas.

## Core Principles

### 1. Statelessness

Maia maintains **zero local state**. All persistence operations are delegated to Atlas:
- Database interactions → `MaiaDAO` (Atlas adapter)
- Object storage → `atlas.vault`
- Event logging → `atlas.events`
- Notifications → `atlas.notifier`

### 2. DAO Pattern

**Rule: Maia NEVER writes raw SQL.**

All database interactions occur through `atlas.adapters.maia.MaiaDAO`:

```python
# ✅ CORRECT
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()
batch = await dao.fetch_hunter_batch(10)

# ❌ WRONG - Never write raw SQL in Maia
async with db.get_connection() as conn:
    await conn.execute("SELECT * FROM search_queue")
```

### 3. Resiliency Strategy (Churn & Burn)

Maia operates in a "disposable container" environment (GitHub Actions, Cloud Run, etc.). When a rate limit (429) is encountered, Maia **immediately terminates** to force container restart and IP rotation:

```python
if resp.status == 429:
    logger.critical("HIT 429 RATE LIMIT. INITIATING CHURN & BURN.")
    raise SystemExit("429 Rate Limit - Container Suicide")
```

**Do NOT catch SystemExit** - it must propagate to terminate the process.

### 4. Key Isolation

Maia uses separate key rings for different agents:
- **Hunter**: Discovery key ring
- **Tracker**: Monitoring key ring
- **Archeology** (future): Analysis key ring

This prevents quota contamination between agents. Configuration is handled by `atlas.config.settings.key_rings`.

## Architecture Components

### The Hunter (Discovery & Ingestion)

**Purpose**: Discover new video content via YouTube search queries.

**Flow**:
1. `fetch_batch()` - Gets queries from `search_queue` using `FOR UPDATE SKIP LOCKED` (race-free concurrency)
2. `search_youtube()` - Executes YouTube API search with key rotation
3. `ingest_results()` - Stores metadata and implements Snowball Effect

**Snowball Effect**: Extract tags from discovered videos and feed them back into the search queue, creating recursive discovery.

**Key Features**:
- Automatic key rotation on 403 (quota exceeded)
- Immediate termination on 429 (Resiliency Strategy)
- Stale token reset (>12h old pagination tokens are discarded)
- Cold archive to vault (metadata stored for reproducibility)
- Hot index to database (queryable structured data)

### The Tracker (Velocity Monitoring)

**Purpose**: Monitor viral velocity by updating view counts and engagement metrics.

**3-Zone Defense Strategy**:
- **Zone 1** (Hot): Videos <24h old → Update hourly
- **Zone 2** (Warm): Videos 1-7 days old → Update every 6 hours
- **Zone 3** (Cold): Videos >7 days old → Update daily

**Flow**:
1. `fetch_targets()` - Gets stale videos based on 3-Zone Defense priority
2. `update_stats()` - Batches up to 50 video IDs per API call
3. Updates both `videos.last_updated_at` and appends to `video_stats_log` (TimescaleDB hypertable)

**Key Features**:
- Batch size enforcement (max 50 for YouTube API)
- Priority-based fetching (Zone 1 > Zone 2 > Zone 3)
- Graceful handling of deleted/private videos

## Data Flow

### Write Path (Hunter)
```
YouTube API → Hunter
           ↓
           ├─→ vault.store_metadata() → Cold Archive
           ├─→ dao.ingest_video_metadata() → Hot Index (videos table)
           ├─→ dao.add_to_search_queue() → Snowball Effect
           └─→ dao.update_search_state() → Update pagination token
```

### Write Path (Tracker)
```
YouTube API → Tracker
           ↓
           ├─→ dao.update_video_stats_batch() → video_stats_log (hypertable)
           └─→ Update last_updated_at → videos table
```

## Error Handling Strategy

### Hunter

| Error | Response |
|-------|----------|
| 403 Quota Exceeded | Rotate to next key in ring, retry |
| 429 Rate Limit | **Raise SystemExit** (Resiliency Strategy) |
| Network Errors | Log warning, skip query |
| Exhausted Key Ring | Log critical, skip query |
| Vault Failures | Log warning, continue (non-blocking) |
| Database Failures | Log error, skip item |

### Tracker

| Error | Response |
|-------|----------|
| 403 Quota Exceeded | Rotate to next key in ring, retry |
| 429 Rate Limit | **Raise SystemExit** (Resiliency Strategy) |
| Network Errors | Log error, return 0 updates |
| Empty API Response | Log warning, return 0 (videos deleted/private) |
| Database Failures | Log error, raise exception |

## Interface with Atlas

### Configuration

Maia uses `atlas.config.settings` singleton. **Never create separate configuration** in Maia.

```python
# ✅ CORRECT
from atlas.config import settings
from atlas.utils import KeyRing

hunter_keys = KeyRing("hunting")

# ❌ WRONG - Don't create Maia-specific config
class MaiaConfig:
    api_key = os.getenv("API_KEY")  # NO!
```

### Database Access

All database methods are in `MaiaDAO`:

```python
# Hunter methods
await dao.fetch_hunter_batch(batch_size)
await dao.ingest_video_metadata(video_data)
await dao.add_to_search_queue(tags)
await dao.update_search_state(topic_id, next_token, count, status)

# Tracker methods
await dao.fetch_tracker_targets(batch_size)
await dao.update_video_stats_batch(updates)
```

### Storage Access

```python
from atlas import vault

# Store raw YouTube API response
vault.store_metadata(video_id, api_response)

# Date is automatically inferred (today's date)
# Path: metadata/{YYYY-MM-DD}/{video_id}.json
```

## Deployment Patterns

### Local Development
```bash
# Install both Atlas and Maia
cd atlas && pip install -e .
cd ../maia && pip install -e .

# Run Hunter
maia-hunter

# Run Tracker
maia-tracker
```

### Docker (Hydra Mode)
```bash
# Build from project root
docker build -f maia/Dockerfile -t pleiades-maia:latest .

# Run Hunter (suicide on 429)
docker run --env-file .env pleiades-maia:latest python -m maia hunter

# Run Tracker
docker run --env-file .env pleiades-maia:latest python -m maia tracker
```

### GitHub Actions (Churn & Burn)
```yaml
- name: Run Hunter
  run: |
    docker run --env-file .env pleiades-maia:latest python -m maia hunter
  continue-on-error: true  # Allow 429 exits

- name: Check Exit Code
  run: |
    if [ $? -eq 429 ]; then
      echo "Rate limit hit - container rotated"
    fi
```

## Testing Strategy

### Unit Tests
Mock all external dependencies:
- `MaiaDAO` methods
- `aiohttp.ClientSession` (YouTube API)
- `atlas.vault`

### Integration Tests
Use `@pytest.mark.integration` marker:
- End-to-end flow tests
- Resiliency Strategy tests (verify SystemExit on 429)
- Edge case handling

### Running Tests
```bash
# Unit tests only (fast)
pytest -m "not integration"

# Integration tests
pytest -m integration

# All tests with coverage
pytest --cov=maia --cov-report=html
```

## Best Practices

### 1. Import Standards
```python
# Always import from Atlas adapters
from atlas.adapters.maia import MaiaDAO
from atlas.utils import KeyRing
from atlas import vault
```

### 2. Prefect Tasks
```python
from prefect import flow, task, get_run_logger

@task(name="descriptive_name")
async def my_task() -> ReturnType:
    logger = get_run_logger()  # Use Prefect logger in tasks
    # ...

@flow(name="descriptive_flow_name")
async def my_flow() -> Dict[str, Any]:
    logger = get_run_logger()
    # Return stats dictionary
    return {"metric": value}
```

### 3. Type Safety
All functions must have complete type hints:
```python
async def fetch_batch(batch_size: int = 10) -> List[Dict[str, Any]]:
    # ...
```

### 4. Resiliency Strategy Implementation
```python
try:
    # ... main logic ...
except SystemExit:
    # Log but DON'T catch - must propagate
    logger.critical("Resiliency Strategy activated")
    raise
except Exception as e:
    # Other errors can be handled
    logger.error(f"Error: {e}")
```

### 5. Logging
```python
# Use get_run_logger() in Prefect tasks/flows
from prefect import get_run_logger
logger = get_run_logger()

# Use module logger elsewhere
import logging
logger = logging.getLogger(__name__)
```

## Performance Considerations

### Hunter
- Batch size: 10 queries (configurable)
- Results per query: 50 videos (YouTube API limit)
- Typical cycle: 2-5 minutes
- Key rotation overhead: <1s per rotation

### Tracker
- Batch size: 50 videos (YouTube API limit)
- 3-Zone Defense ensures hot videos get priority
- Typical cycle: 30-60 seconds
- Updates written to hypertable (optimized for time-series)

## Future Enhancements

1. **Circuit Breaker**: Temporary key suspension on repeated 403s
2. **Metrics**: Prometheus instrumentation for cycle times
3. **Adaptive Batching**: Dynamic batch sizes based on quota remaining
4. **Smart Retry**: Exponential backoff with jitter
5. **Channel Tracking**: Extend Tracker to monitor channel-level stats

## Consistency Checklist

When making changes to Maia, verify:

- [ ] No raw SQL (all queries in `MaiaDAO`)
- [ ] No local state persistence
- [ ] Resiliency Strategy respected (429 → SystemExit)
- [ ] All functions have type hints
- [ ] Tests added for new functionality
- [ ] Error handling follows strategy above
- [ ] Configuration uses `atlas.config.settings`
- [ ] Imports from Atlas are correct
- [ ] Documentation updated
- [ ] Logging uses appropriate logger

## Troubleshooting

### "Empty KeyRing" Error
**Cause**: No keys configured in `YOUTUBE_API_KEY_POOL_JSON`
**Solution**: Add API keys to `.env` file

### "HF_DATASET_ID required" Error
**Cause**: VAULT_PROVIDER=huggingface but HF credentials missing
**Solution**: Set `HF_DATASET_ID` and `HF_TOKEN` in `.env`

### Tests Failing with Import Errors
**Cause**: Atlas not installed or not in editable mode
**Solution**: `cd atlas && pip install -e .`

### Hunter/Tracker Not Finding Videos
**Cause**: Empty `search_queue` or no stale videos
**Solution**: Check database tables, manually insert test queries

---

**Version**: 0.1.0  
**Last Updated**: 2026-01-10  
**Maintainer**: Ahmad Saeed Zaidi

