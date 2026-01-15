# Pleiades Architecture

**System design and component interactions**

---

## Project Structure

```
pleiades/
├── pyproject.toml              # ROOT: Workspace & dev tools (Black, Isort)
├── README.md
├── docs/                        # Unified documentation
│
├── atlas/                       # LIBRARY: Shared Infrastructure
│   ├── pyproject.toml           # Deps: Pydantic, Psycopg, GCS-Client
│   ├── src/
│   │   └── atlas/
│   │       ├── __init__.py
│   │       ├── db.py            # PostgreSQL connection pool
│   │       ├── vault.py         # HF/GCS storage abstraction
│   │       ├── config.py        # Settings management
│   │       ├── events.py        # Observer pattern event bus
│   │       ├── notifier.py      # Alerts and notifications
│   │       ├── utils.py         # KeyRing, HydraExecutor
│   │       ├── schema.sql       # Database schema
│   │       └── adapters/
│   │           ├── maia.py      # MaiaDAO (data access)
│   │           └── maia_ghost.py # Ghost Tracking mixin
│   ├── docs/                    # Atlas-specific docs
│   └── tests/                   # Atlas unit tests
│
├── maia/                        # SERVICE: Video Collection
│   ├── pyproject.toml           # Deps: Atlas (local), Google-API-Client
│   ├── Dockerfile               # Slim container
│   ├── src/
│   │   └── maia/
│   │       ├── __init__.py
│   │       ├── hunter/
│   │       │   └── flow.py      # Discovery agent
│   │       ├── tracker/
│   │       │   └── flow.py      # Monitoring agent (Ghost Tracking)
│   │       ├── janitor/
│   │       │   └── flow.py      # Cleanup agent (Hot Queue)
│   │       ├── painter/
│   │       │   └── flow.py      # Metadata enrichment
│   │       └── scribe/
│   │           └── flow.py      # Feature extraction
│   ├── docs/                    # Maia-specific docs
│   └── tests/                   # Maia unit tests
│
└── alkyone/                     # SERVICE: Integration Testing
    ├── pyproject.toml           # Deps: Pytest, Httpx, VCRpy
    ├── src/
    │   └── alkyone/
    │       └── fixtures.py      # Shared test fixtures
    └── tests/
        └── components/          # Integration tests by component
            ├── atlas/
            └── maia/
```

---

## Core Components

### Atlas - Infrastructure Library

**Purpose**: Shared infrastructure for all Pleiades services

**Modules**:

#### 1. Database (`atlas.db`)
PostgreSQL connection management:
```python
from atlas import db

await db.initialize()
async with db.get_connection() as conn:
    result = await conn.fetchrow("SELECT * FROM videos LIMIT 1")
```

#### 2. Vault (`atlas.vault`)
Abstract storage for HuggingFace or GCS:
```python
from atlas import vault

# Store metadata
vault.store_metadata("video_123", data)

# Append time-series metrics (Ghost Tracking)
vault.append_metrics([
    {"video_id": "123", "views": 1000, "timestamp": "..."}
])
```

#### 3. Events (`atlas.events`)
Observer pattern event bus:
```python
from atlas import events

@events.on("video.discovered")
async def on_video_discovered(data: dict):
    logger.info(f"New video: {data['video_id']}")

await events.emit("video.discovered", {"video_id": "123"})
```

#### 4. Notifier (`atlas.notifier`)
Alerts and notifications:
```python
from atlas import notifier

await notifier.send(
    level="warning",
    message="API quota low",
    metadata={"remaining_keys": 2}
)
```

#### 5. Utils (`atlas.utils`)
- **KeyRing**: API key pool management
- **HydraExecutor**: Automatic retry with key rotation
```python
from atlas.utils import KeyRing, HydraExecutor

keys = KeyRing("hunting")
executor = HydraExecutor(keys, agent_name="hunter")
result = await executor.execute_async(make_request)
```

#### 6. Data Access (`atlas.adapters.maia`)
DAO pattern for SQL operations:
```python
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()

# Search queue operations
await dao.add_to_search_queue(["query1", "query2"])
batch = await dao.fetch_hunter_batch(10)

# Video operations
await dao.ingest_video_metadata(video_data)

# Ghost Tracking operations
await dao.add_to_watchlist(video_id)
batch = await dao.fetch_tracking_batch(50)
await dao.update_watchlist_schedule(updates)

# Janitor operations
result = await dao.run_janitor()
```

---

### Maia - Collection Service

**Purpose**: Discover, track, and analyze YouTube content

**Agents**:

#### 1. Hunter (Discovery)
Searches YouTube for new videos:

```python
@flow(name="run_hunter_cycle")
async def run_hunter_cycle(batch_size: int = 10):
    # 1. Fetch search queries
    queries = await dao.fetch_hunter_batch(batch_size)
    
    # 2. Search YouTube API
    for query in queries:
        results = await youtube_api.search(query["query_term"])
        
        # 3. Ingest videos
        for video in results:
            await dao.ingest_video_metadata(video)
            await dao.add_to_watchlist(video["id"])  # Ghost Tracking
        
        # 4. Update pagination
        await dao.update_search_state(query["id"], next_page_token)
```

**Features**:
- Hydra Protocol for key management
- Snowball effect (adds related queries)
- Hot Queue integration

#### 2. Tracker (Monitoring)
Monitors video statistics:

```python
@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50):
    # 1. Fetch from watchlist (Ghost Tracking)
    videos = await dao.fetch_tracking_batch(batch_size)
    
    # 2. Query YouTube API
    stats = await youtube_api.get_statistics(video_ids)
    
    # 3. Store to Vault (Parquet)
    vault.append_metrics(metrics_data)
    
    # 4. Update tracking schedule
    for video in videos:
        tier, next_time = dao.calculate_next_track_time(video["published_at"])
        updates.append({
            "video_id": video["id"],
            "tracking_tier": tier,
            "next_track_at": next_time
        })
    
    await dao.update_watchlist_schedule(updates)
```

**Features**:
- Ghost Tracking (infinite monitoring)
- Adaptive tiers (HOURLY → DAILY → WEEKLY)
- Hydra Protocol integration

#### 3. Janitor (Cleanup)
Enforces 7-day retention:

```python
@flow(name="run_janitor_cycle")
async def run_janitor_cycle():
    # Delete videos older than 7 days
    result = await dao.run_janitor()
    
    logger.info(f"Cleaned {result['deleted']} videos")
```

**Features**:
- Hot Queue management
- Safety checks
- Watchlist protection

#### 4. Painter (Enrichment)
Enriches metadata with external APIs:

```python
@flow(name="run_painter_cycle")
async def run_painter_cycle():
    # Fetch unenriched videos
    videos = await dao.fetch_painter_targets()
    
    # Enrich with external data
    for video in videos:
        enriched = await enrich_metadata(video)
        await dao.update_video_metadata(enriched)
```

#### 5. Scribe (Feature Extraction)
Extracts features for ML:

```python
@flow(name="run_scribe_cycle")
async def run_scribe_cycle():
    # Fetch unprocessed videos
    videos = await dao.fetch_scribe_targets()
    
    # Extract features
    for video in videos:
        features = extract_features(video)
        await dao.store_features(features)
```

---

### Alkyone - Integration Testing

**Purpose**: End-to-end validation of all components

**Test Categories**:

#### Integration Tests
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_cycle_complete_flow():
    """Test complete Hunter cycle from fetch to ingest."""
    stats = await run_hunter_cycle(batch_size=1)
    assert stats["videos_discovered"] > 0
```

#### Smoke Tests
```python
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_database_connectivity():
    """Verify database connection."""
    is_healthy = await db.health_check()
    assert is_healthy
```

#### Validation Tests
```python
@pytest.mark.asyncio
async def test_tracker_handles_deleted_videos():
    """Test Ghost Tracking continues after video deletion."""
    # ... test implementation
```

---

## Data Flow

### Discovery Flow

```
1. Hunter fetches search query from search_queue
   ↓
2. Hunter queries YouTube API (Hydra Protocol)
   ↓
3. Hunter ingests video to videos table (Hot Queue)
   ↓
4. Hunter adds video to watchlist (Ghost Tracking)
   ↓
5. Hunter updates search_queue with next_page_token
```

### Tracking Flow (Ghost Mode)

```
1. Tracker fetches from watchlist (not videos!)
   ↓
2. Tracker queries YouTube API (Hydra Protocol)
   ↓
3. Tracker stores metrics to Vault (Parquet)
   ↓
4. Tracker calculates next tier based on video age
   ↓
5. Tracker updates watchlist schedule
```

### Cleanup Flow

```
1. Janitor scans videos table
   ↓
2. Janitor identifies videos >7 days old
   ↓
3. Janitor deletes from videos (NOT watchlist!)
   ↓
4. Ghost Tracking continues (watchlist intact)
```

---

## Key Patterns

### 1. DAO Pattern

All SQL access goes through Data Access Objects:

```python
# ✅ GOOD
dao = MaiaDAO()
videos = await dao.fetch_hunter_batch(10)

# ❌ BAD
async with db.get_connection() as conn:
    result = await conn.fetch("SELECT * FROM videos")
```

### 2. Stateless Agents

Agents are stateless and idempotent:

```python
# ✅ GOOD - Stateless
async def run_hunter_cycle(batch_size: int):
    dao = MaiaDAO()  # New instance each cycle
    # ... processing

# ❌ BAD - Stateful
class Hunter:
    def __init__(self):
        self.state = {}  # Avoid state!
```

### 3. Observer Pattern

Use events for loose coupling:

```python
# Component A emits
await events.emit("video.discovered", {"video_id": "123"})

# Component B reacts
@events.on("video.discovered")
async def on_video_discovered(data: dict):
    await process_new_video(data)
```

### 4. Hydra Protocol

All external API calls use HydraExecutor:

```python
# ✅ GOOD
executor = HydraExecutor(keys, agent_name="hunter")
result = await executor.execute_async(make_request)

# ❌ BAD
key = keys.next_key()
response = await make_request(key)  # No rotation!
```

---

## Deployment

### Docker Compose

```yaml
version: '3.8'

services:
  maia-hunter:
    build: ./maia
    environment:
      - ENV=prod
      - DATABASE_URL=${DATABASE_URL}
      - YOUTUBE_API_KEY_POOL_JSON=${YOUTUBE_API_KEY_POOL_JSON}
    restart: on-failure:5
    command: python -m maia.hunter.flow

  maia-tracker:
    build: ./maia
    environment:
      - ENV=prod
      - DATABASE_URL=${DATABASE_URL}
    restart: on-failure:5
    command: python -m maia.tracker.flow
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: maia-hunter
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: hunter
        image: pleiades/maia:latest
        command: ["python", "-m", "maia.hunter.flow"]
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: pleiades-secrets
              key: database-url
```

---

## Configuration

### Environment Variables

**Atlas (.env)**:
```bash
DATABASE_URL=postgresql://user:pass@host:5432/db
VAULT_PROVIDER=huggingface
HF_DATASET_ID=username/dataset
HF_TOKEN=hf_token
ENV=prod
```

**Maia (.env)**:
```bash
YOUTUBE_API_KEY_POOL_JSON='["key1", "key2"]'
HYDRA_ENABLED=true
JANITOR_RETENTION_DAYS=7
```

---

## Performance

### Throughput

- **Hunter**: 5,000 videos/hour (limited by API quota)
- **Tracker**: 50,000 videos/hour (batch API calls)
- **Janitor**: 100,000 deletions/minute

### Latency

- **Database queries**: <10ms (indexed)
- **Vault writes**: <500ms (batch)
- **API calls**: 200-500ms (YouTube API)

### Resource Usage

- **SQL**: <0.5 GB (Hot Queue + Watchlist)
- **Vault**: Unlimited (compressed Parquet)
- **Memory**: 256MB per agent
- **CPU**: 0.5 core per agent

---

## Summary

Pleiades architecture enables:

- ✅ **High throughput** (100k+ videos/day)
- ✅ **Low SQL footprint** (<0.5 GB)
- ✅ **Infinite tracking** (Ghost Tracking)
- ✅ **Resilient API usage** (Hydra Protocol)
- ✅ **Clean separation** (Atlas ↔ Maia ↔ Alkyone)
- ✅ **Stateless agents** (Easy scaling)

**Result**: Scalable, maintainable viral video intelligence platform.
