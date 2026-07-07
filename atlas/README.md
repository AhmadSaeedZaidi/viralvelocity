# Atlas — Infrastructure Library for Pleiades

**Version 0.2.1**

Atlas is the core infrastructure library for the Pleiades platform. It provides database connectivity, object storage, event sourcing, notifications, and YouTube API helpers used by the Maia agents. Atlas has zero agent-logic — it implements plumbing.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                   ATLAS LAYERS                                   │
│                                                                                  │
│   ┌──────────┐    ┌────────────┐    ┌───────────────┐                            │
│   │  Config  │    │     DB     │    │   Adapters    │                            │
│   │ Settings │──▶ │ Connection │──▶ │  (Protocols)  │                            │
│   │(Pydantic)│    │  Manager   │    │               │                            │
│   └──────────┘    └────────────┘    └───────┬───────┘                            │
│                                             │                                    │
│                     ┌───────────────────────┼───────────────────────┐            │
│                     ▼                       ▼                       ▼            │
│            ┌─────────────────┐      ┌───────────────┐     ┌───────────────────┐  │
│            │     Models      │      │ Repositories  │     │     Services      │  │
│            │(Pydantic domain)│────▶ │   (DAO/Repo   │───▶ │ Vault · Events    │  │
│            │                 │      │   pattern)    │     │ Notifications     │  │
│            │                 │      │               │     │ YouTube API       │  │
│            │                 │      │               │     │ KeyRing · Retry   │  │
│            └─────────────────┘      └───────────────┘     └───────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Design Patterns

| Pattern | Where |
|---|---|
| **Repository** | `VideoRepository`, `ChannelRepository`, etc. — domain-focused data access |
| **Data Access Object** | `DatabaseAdapter` — low-level SQL primitives (execute, fetch, scalar) |
| **Singleton** | `DatabaseManager`, `Settings`, `EventBus`, `DiscordNotifier`, Vault instance |
| **Strategy** | `VaultStrategy` — interchangeable HF/GCS backends |
| **Protocol (Duck Typing)** | `ConnectionProvider` — any async context manager yielding connections |
| **Adaptive Scheduling** | `WatchlistRepository.calculate_next_track_time` — tiered tracking intervals |

---

## Modules

### 1. Configuration — `config.py`

Pydantic `BaseSettings` loaded from `.env` or environment variables.

```python
from atlas.config import settings

settings.DATABASE_URL       # PostgreSQL DSN
settings.API_KEYS            # list[str] — all YouTube API keys
settings.KEY_RINGS           # dict[str, list[str]] — split into hunting/tracking/archeology pools
settings.VAULT_PROVIDER      # "huggingface" | "gcs"
settings.JANITOR_RETENTION_DAYS  # default 7
settings.COMPLIANCE_MODE     # when True, enforces vault config validation
```

Key features:
- `api_keys` property parses `YOUTUBE_API_KEY_POOL_JSON` into a list
- `key_rings` property splits keys into 3 pools by configurable sizes
- `validate_vault_config` model_validator ensures HF/GCS credentials are present based on provider
- `youtube_cookies_resolved_path` resolves cookie file path

### 2. Database Manager — `db.py`

Singleton `DatabaseManager` wrapping `psycopg` async connection pool.

```python
from atlas import db

await db.initialize()                                    # Open pool
async with db.get_connection() as conn:                  # Get connection
    await conn.execute("SELECT 1")
await db.health_check()                                  # True/False
await db.setup_test_schema()                             # Create tables for tests
await db.reset_for_test()                                # TRUNCATE all tables
await db.close()                                         # Close pool
```

- Uses `AsyncConnectionPool` from psycopg (0 min, 20 max connections)
- Singleton: `atlas.db.db` is the one instance
- Exposes `get_connection` as an async context manager

### 3. Database Adapter — `adapters/__init__.py`

Base class for all repositories. Wraps common SQL patterns:

```python
from atlas.adapters import DatabaseAdapter

class MyRepo(DatabaseAdapter):
    async def find(self, id: str) -> dict | None:
        return await self._fetch_one("SELECT * FROM x WHERE id = %s", (id,))

    async def list_all(self) -> list[dict]:
        return await self._fetch_all("SELECT * FROM x")
```

Available primitives:
| Method | Returns |
|---|---|
| `_execute(query, params)` | `None` |
| `_fetch_one(query, params)` | `dict \| None` |
| `_fetch_all(query, params)` | `list[dict]` |
| `_fetch_many(query, params, limit)` | `list[dict]` |
| `_execute_many(query, params_list)` | `None` |
| `_fetch_scalar(query, params)` | `Any \| None` |

Constructor accepts optional `ConnectionProvider`. Defaults to the global `DatabaseManager` singleton.

### 4. Domain Models — `models/`

9 Pydantic models with `from_attributes=True` (ORM-compatible):

| Model | Key Fields |
|---|---|
| `Video` | id, title, channel_id, published_at, duration, tags, status (PENDING→PROCESSING→PROCESSED→ARCHIVED→FAILED) |
| `VideoStats` | video_id, timestamp, views, likes, comment_count |
| `Channel` | id, title, country, custom_url, is_verified |
| `ChannelStats` | channel_id, timestamp, view_count, subscriber_count, video_count |
| `ChannelHistory` | channel_id, changed_at, old_title, new_title, event_type |
| `Transcript` | video_id, language, vault_uri, created_at |
| `SystemEvent` | id, event_type, entity_id, payload, created_at |
| `SearchQueueItem` | id, query_term, priority, next_page_token, status |
| `WatchlistItem` | video_id, tracking_tier (HOURLY/DAILY/WEEKLY), next_track_at |

### 5. Repositories — `repositories/`

5 repositories extending `DatabaseAdapter`:

#### `VideoRepository`
The largest repository. Manages the full video lifecycle:

- **Ingestion**: `ingest_video_metadata()` — parses YouTube API response, upserts video + channel
- **Claiming**: `claim_scribe_batch()`, `claim_painter_batch()` — atomic `SELECT ... FOR UPDATE SKIP LOCKED`
- **Tracking**: `fetch_tracker_targets()` — 3-tier zone query (HOURLY/DAILY/WEEKLY from watchlist)
- **Stats**: `log_stats_batch()`, `update_stats_batch()`, `get_latest_stats()`
- **Archival**: `archive_video_batch()` — vault hand-off with verification + purge (janitor Phase 3)
- **Cold stats**: `archive_cold_stats()` — moves old `video_stats_log` rows to vault
- **State transitions**: `mark_transcript_safe()`, `mark_visuals_safe()`, `mark_done()`, `mark_failed()`, `mark_archived()`
- **Cleanup**: `run_janitor()` — deletes old processed videos (legacy)

#### `ChannelRepository`
- `save()` / `get_by_id()` — basic CRUD
- `ingest_channel_snapshot()` — parses API response, upserts + logs stats
- `log_stats()` / `get_latest_stats()` — time-series channel metrics
- `needs_refresh()` — checks if channel data is stale (configurable max_age_hours)

#### `WatchlistRepository`
Implements Adaptive Scheduling tier logic:

- `add(video_id, tier)` — insert with ON CONFLICT DO NOTHING
- `fetch_batch(batch_size)` — `FOR UPDATE SKIP LOCKED` where `next_track_at <= NOW()`
- `update_schedule()` — batch-updates tracking schedules
- `calculate_next_track_time()` — returns (tier_name, next_track_at) based on video age:
  - **HOURLY**: next 1 hour (videos < 7 days old)
  - **DAILY**: next 24 hours (videos < 90 days old)
  - **WEEKLY**: next 7 days (videos 90+ days old)

#### `EventRepository`
Immutable event log:

- `emit(event_type, entity_id, payload)` — inserts with UUID
- `get_by_entity()`, `get_by_type()`, `get_recent()` — query methods

#### `SearchQueueRepository`
Discovery terms queue:

- `fetch_batch(batch_size)` — ordered by priority DESC, mention_count DESC, FOR UPDATE SKIP LOCKED
- `update_state()` — sets next_page_token, result_count after search
- `add_terms(terms)` — batch insert with dedup, increments mention_count on conflict

### 6. Vault — `vault.py`

Abstract `VaultStrategy` with two implementations:

| Operation | HF Implementation | GCS Implementation |
|---|---|---|
| `store_json(path, data)` | Upload JSON to HF dataset | Write to GCS bucket |
| `fetch_json(path)` | Download from HF dataset | Read from GCS bucket |
| `store_binary(path, data)` | Upload bytes | Write bytes |
| `store_visual_evidence(video_id, frames)` | Upload screenshot frames | Same |
| `append_metrics(metrics_data)` | Append to Parquet on HF | Append to Parquet on GCS |
| `list_files(prefix)` | List HF dataset files | List GCS objects |

```python
from atlas.vault import get_vault

vault = get_vault()                          # lazy singleton
await vault.store_json("videos/id.json", {"title": "..."})
data = await vault.fetch_json("videos/id.json")
```

Provider selection via `VAULT_PROVIDER= huggingface | gcs`. Default is huggingface.

### 7. Event Bus — `events.py`

```python
from atlas import events

await events.emit("video.discovered", "VIDEO_123", {"title": "..."})
```

Appends to `system_events` table. Used by Maia agents to signal milestones (batch complete, archivals, failures).

### 8. Notifications — `notifications.py`

Discord webhook notifications with embed formatting:

```python
from atlas import notifier, AlertChannel, AlertLevel

await notifier.send(
    title="Hunter Cycle Complete",
    description="Discovered 12 new videos",
    channel=AlertChannel.HUNT,
    level=AlertLevel.SUCCESS,
    fields=[("batch_size", "10"), ("new", "12")],
)
```

4 channels: `ALERTS`, `HUNT`, `SURVEILLANCE`, `OPS`. Configure webhook URLs in `.env`.

### 9. YouTube API Helpers — `youtube.py`

Thin wrappers over the YouTube Data API v3 with key rotation:

```python
from atlas.youtube import lookup_videos, lookup_channels

videos = await lookup_videos(["VIDEO_ID_1", "VIDEO_ID_2"])
channels = await lookup_channels(["CHANNEL_ID_1"])
```

- Batches requests (max 50 IDs per call)
- Uses `KeyRing` + `ResiliencyExecutor` for key rotation
- No agent-specific logic — just HTTP + JSON parsing

### 10. Utilities — `utils.py`

#### `KeyRing`
Round-robin YouTube API key management:

```python
keys = KeyRing("hunting")
keys.next_key()                    # cycle through pool
session_id = keys.start_session()  # track per-session key usage
keys.attempt_rotation(session_id)  # True if another key available
```

Key pools are configured via `KEY_POOL_*_SIZE` env vars. Keys rotate infinitely.

#### `ResiliencyExecutor`
Handles API key exhaustion with the Resiliency Strategy:

```python
executor = ResiliencyExecutor(key_ring, agent_name="hunter")
result = await executor.execute_async(make_request)
```

- On 403/429: rotates key and retries
- On all keys exhausted: `sys.exit(0)` — clean container death, orchestration will restart
- Supports custom error classifiers

#### Helpers

| Function | Purpose |
|---|---|
| `retry_async(max_attempts, delay, backoff)` | Decorator for retry with exponential backoff |
| `health_check_all()` | Check DB health |
| `validate_youtube_id(id)` | Validate 11-char base64url video ID |
| `validate_channel_id(id)` | Validate 24-char UC-prefixed channel ID |
| `execute_youtube_request_async(keys, request_func)` | Convenience wrapper |

### 11. Schema Manager — `setup.py`

```python
from atlas.setup import provision_schema

await provision_schema()  # creates all tables, indexes, and extensions
```

Used by `make setup`. Loads `schema.sql` which includes TimescaleDB hypertables for `video_stats_log` and `channel_stats_log`.

---

## Quick Start

```bash
cd atlas
poetry install --extras all --with dev
cp ENV.example .env   # edit with your credentials
make setup            # provision database schema
make test             # run unit tests
```

### Dependencies

| Extra | Packages |
|---|---|
| *(core)* | pydantic, pydantic-settings, psycopg[binary,pool], aiohttp, orjson |
| `[hf]` | huggingface-hub, pandas, pyarrow |
| `[gcs]` | google-cloud-storage |
| `[orchestration]` | prefect |
| `[all]` | everything above |

---

## Configuration Reference

See [ENV.example](ENV.example) for all options. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL DSN |
| `VAULT_PROVIDER` | `huggingface` | `huggingface` or `gcs` |
| `HF_DATASET_ID` | — | HuggingFace dataset repo |
| `HF_TOKEN` | — | HF API token |
| `YOUTUBE_API_KEY_POOL_JSON` | — | JSON array of API keys |
| `COMPLIANCE_MODE` | `true` | Enforce vault config validation |
| `JANITOR_RETENTION_DAYS` | `7` | Days before archiving videos |
| `KEY_POOL_*_SIZE` | `1` | Keys to allocate per agent pool |
| `DISCORD_WEBHOOK_*` | — | Webhook URLs for notification channels |

---

## Design Notes

- **Stateless**: Atlas has no agent logic, no workflows, no long-running processes. It's a library.
- **No hard dependencies on optional providers**: HF/GCS imports are guarded by `try/except`. Missing provider libraries don't break core functions.
- **Type-safe**: All models use Pydantic v2 with `from_attributes=True` for DB compatibility.
- **Thread-safe vault offload**: Vault operations run via `asyncio.to_thread` to avoid blocking the event loop.
- **No ORM**: Raw SQL with repository pattern. Explicit queries for performance and control.

---

## License

MIT
