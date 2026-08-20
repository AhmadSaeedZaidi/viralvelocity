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
│   │       └── adapters/        # Low-level DB adapter (DatabaseAdapter)
│   │       └── repositories/    # One Repository class per entity (Video, Channel, ...)
│   ├── docs/                    # Atlas-specific docs
│   └── tests/                   # Atlas unit tests
│
├── maia/                        # SERVICE: Video Collection
│   ├── pyproject.toml           # Deps: Atlas (local), Google-API-Client
│   ├── Dockerfile               # Single image (atlas + maia)
│   ├── src/
│   │   └── maia/
│   │       ├── __init__.py
│   │       ├── registry.py      # AGENT_REGISTRY (name → class)
│   │       ├── hunter/          # Discovery agent (producer)
│   │       ├── tracker/         # Monitoring agent (consumer)
│   │       ├── janitor/         # Cleanup agent (consumer)
│   │       ├── painter/         # Keyframe extraction (consumer)
│   │       ├── scribe/          # Captions extraction (consumer)
│   │       ├── streamer/        # Unified YouTube fetch -> vault `raw` (producer)
│   │       ├── singer/          # Audio extraction from `raw` (consumer)
│   │       ├── muralist/        # Full-video archival (consumer, manual-only)
│   │       ├── media/           # Shared YouTube streamer (single yt-dlp path)
│   │       └── heartbeat/       # Fleet status reporter
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

# Append time-series metrics (Adaptive Scheduling)
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

#### 6. Data Access (`atlas.repositories`)
Repository pattern — one class per entity, returning validated domain models
(see `refactor_draft.md` for the rationale behind retiring the old `MaiaDAO`):
```python
from atlas.repositories import VideoRepository, SearchQueueRepository

video_repo = VideoRepository()
search_repo = SearchQueueRepository()

# Video operations
videos = await video_repo.claim_streamer_batch(10)
await video_repo.mark_fetched(video_id, raw_uri)

# Search queue operations
await search_repo.add_to_search_queue(["query1", "query2"])

# Adaptive Scheduling operations
await video_repo.add_to_watchlist(video_id)
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
            await dao.add_to_watchlist(video["id"])  # Adaptive Scheduling
        
        # 4. Update pagination
        await dao.update_search_state(query["id"], next_page_token)
```

**Features**:
- Resiliency Strategy for key management
- Snowball effect (adds related queries)
- Tiered Storage integration
- Quality gate: per-video signal + Shorts HEAD probe + AI-slop denylist + channel-statistics AI-farm filter, applied before ingest/snowball

#### 2. Tracker (Monitoring)
Monitors video statistics:

```python
@flow(name="run_tracker_cycle")
async def run_tracker_cycle(batch_size: int = 50):
    # 1. Fetch from watchlist (Adaptive Scheduling)
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
- Adaptive Scheduling (infinite monitoring)
- Adaptive tiers (HOURLY → DAILY → WEEKLY)

> **Deployed**: Watchlist-driven adaptive scheduling is live and verified
> (2026-08-03, local `pleiades` DB). Every discovered video joins the persistent
> `watchlist` at tier `HOURLY`, tiers self-decay to `DAILY`/`WEEKLY` by age via
> `calculate_next_track_time`, and `update_schedule` advances `next_track_at`.
> The tracker drives cycles through `fetch_targets_task`/`update_stats_task`.
- Resiliency Strategy integration

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
- Tiered Storage management
- Safety checks
- Watchlist protection

#### 4. Painter (Frame Extraction)
Extracts keyframes from the `raw` artifact that streamer stored in the vault
(range-requested, so no full download) and sets `has_visuals`:

```python
@flow(name="run_painter_cycle")
async def run_painter_cycle():
    videos = await video_repo.claim_painter_batch()
    for video in videos:
        frames = await extract_frames(video)   # range-requested from vault raw
        await vault.store_frames(video.id, frames)
        await video_repo.mark_visuals_safe(video.id)
```

#### 5. Scribe (Transcript / Caption Extraction)
Owns transcripts. Its **primary** path fetches YouTube captions directly via
yt-dlp (`TranscriptLoader`) — free, and needs no audio. Only when no captions
exist does it fall back to speech-to-text on the singer's stored audio (or a
YouTube audio download as a last resort), transcribed via the Grok → Mistral
cascade. It sets `has_transcript`. Scribe is intentionally **audio-independent**
and is claimed without waiting for the singer:

```python
@flow(name="run_scribe_cycle")
async def run_scribe_cycle():
    videos = await video_repo.claim_scribe_batch()   # audio-independent
    for video in videos:
        transcript = await transcribe(video)
        await vault.store_transcript(video.id, transcript)
        await video_repo.mark_transcript_safe(video.id)
```

#### 6. Streamer, Singer & Muralist (Audio / Video pipeline)

The audio and video media now flow through a small producer/consumer set
(see `maia/README.md` for the authoritative detail):

- **Streamer** (producer) — performs one unified YouTube pull (audio + frames),
  stores the `raw` artifact in the vault, and flips `fetched = TRUE`. It does
  **not** set `has_audio` / `has_visuals` itself.
- **Singer** (consumer) — extracts the speech track from the stored `raw` locally
  (no YouTube rate limit), stores `audio/{id}.opus`, and sets `has_audio`.
- **Scribe** (consumer) — fetches YouTube captions directly (free), falling back
  to STT on the singer's audio only when captions are missing; sets
  `has_transcript`. It is **not** serialized behind the singer.
- **Muralist** (consumer, manual-only) — archives the full source clip to
  `videos/{id}.mp4` (`has_video`); not fleet-scheduled (storage-heavy). It
  consumes the `raw` artifact, so `raw` is only reclaimed after muralist runs
  (or once a `raw_ttl` elapses) — see `reclaim_raw_if_complete`.

All of streamer/singer/painter/muralist share the single YouTube stream path in
`maia/media/streamer.py` (yt-dlp + Deno PoToken + `bgutil` PO-token provider).

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
    """Test Adaptive Scheduling continues after video deletion."""
    # ... test implementation
```

---

## Data Flow

### Discovery Flow

```
1. Hunter fetches search query from search_queue
   ↓
2. Hunter queries YouTube API (Resiliency Strategy)
   ↓
3. Hunter ingests video to videos table (Tiered Storage)
   ↓
4. Hunter adds video to watchlist (Adaptive Scheduling)
   ↓
5. Hunter updates search_queue with next_page_token
```

### Tracking Flow (Ghost Mode)

```
1. Tracker fetches from watchlist (not videos!)
   ↓
2. Tracker queries YouTube API (Resiliency Strategy)
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
4. Adaptive Scheduling continues (watchlist intact)
```

---

## Key Patterns

### 1. Repository Pattern

All SQL access goes through per-entity Repositories (one class per entity,
returning validated domain models). This replaced the old monolithic `MaiaDAO`
— see `refactor_draft.md`:

```python
# ✅ GOOD
video_repo = VideoRepository()
videos = await video_repo.claim_streamer_batch(10)

# ❌ BAD
async with db.get_connection() as conn:
    result = await conn.fetch("SELECT * FROM videos")
```

### 2. Stateless Agents

Agents are stateless and idempotent:

```python
# ✅ GOOD - Stateless
async def run_hunter_cycle(batch_size: int):
    video_repo = VideoRepository()  # New instance each cycle
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

### 4. Resiliency Strategy

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
    command: python -m maia hunter

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
        command: ["python", "-m", "maia", "hunter"]
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

- **SQL**: <0.5 GB (Tiered Storage + Watchlist)
- **Vault**: Unlimited (compressed Parquet)
- **Memory**: 256MB per agent
- **CPU**: 0.5 core per agent

---

## Summary

Pleiades architecture enables:

- ✅ **High throughput** (100k+ videos/day)
- ✅ **Low SQL footprint** (<0.5 GB)
- ✅ **Infinite tracking** (Adaptive Scheduling)
- ✅ **Resilient API usage** (Resiliency Strategy)
- ✅ **Clean separation** (Atlas ↔ Maia ↔ Alkyone)
- ✅ **Stateless agents** (Easy scaling)
- ✅ **Corpus-quality tooling** (`quality-report` monitor, `purge` for short/low-value videos with overwrite-on-reprocess)

**Result**: Scalable, maintainable viral video intelligence platform.
