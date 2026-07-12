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
settings.COMPLIANCE_MODE     # opt-in policy enforcement; never collapses the key pool
```

Key features:
- `api_keys` property parses `YOUTUBE_API_KEY_POOL_JSON` into a list
- `key_rings` property splits keys into 3 pools (hunting / tracking / archeology)
- `effective_pool_sizes()` returns the ring sizes in effect — a **dynamic
  allocation cache** (written weekly by the janitor, see `key_pool.py`) takes
  precedence over the static `KEY_POOL_*_SIZE` env vars
- `validate_vault_config` model_validator ensures HF/GCS credentials are present based on provider
- `youtube_cookies_resolved_path` resolves cookie file path

#### Dynamic key pools — `key_pool.py`

`archeology` is a fixed reserve; `tracking` scales step-wise with the size of the
video corpus (more videos ⇒ more `videos.list` throughput needed to keep stats
fresh); `hunting` takes the remainder (discovery is prioritised while the corpus
is small).

| Corpus size | hunting | tracking | archeology |
|---|---|---|---|
| < 10k | 19 | 2 | 3 |
| 10k–100k | 17 | 4 | 3 |
| 100k–500k | 13 | 8 | 3 |
| 500k+ | 9 | 12 | 3 |

*(example for a 24-key pool)*. The janitor's Phase 0 calls
`refresh_allocation()` each cycle, but it only recomputes when the cache is older
than `REFRESH_INTERVAL_DAYS` (7). The result is cached to
`data/pool_allocation.json` (override with `KEY_POOL_ALLOCATION_PATH`) so the
frequently-restarting agents read a stable value without querying the database.
Corpus size is read via `VideoRepository.count_videos()` (an O(1) `reltuples`
estimate).

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
- **Tracking**: `fetch_tracker_targets()` — 3-tier recency-zone query (fresh → older) that prioritises recently published videos for stats refresh
- **Stats**: `log_stats_batch()`, `update_stats_batch()`, `get_latest_stats()`
- **Transcripts**: `record_transcript()` — upserts the `transcripts` pointer row (video_id → vault_uri) written by the Scribe
- **Archival**: `archive_video_batch()` — vault hand-off with verification + purge (janitor Phase 3)
- **Cold stats**: `archive_cold_stats()` — moves old `video_stats_log` rows to vault
- **State transitions**: `mark_transcript_safe()`, `mark_visuals_safe()`, `mark_done()`, `mark_failed()`, `mark_archived()`, `release_to_pending()` (re-queue a claimed video after a transient failure such as rate limiting)
- **Cleanup**: `run_janitor()` — deletes old processed videos (legacy)

**Mixin composition.** `VideoRepository` is assembled from focused mixins in
`repositories/video/` — `VideoIngestionMixin`, `VideoTrackingMixin`,
`VideoStateMixin`, `VideoJanitorMixin` (all extending `DatabaseAdapter`). Where a
mixin calls a method defined on a sibling (e.g. the janitor calling
`get_latest_stats`), the method types `self` as `VideoRepositoryProtocol`
(`repositories/video/protocols.py`) — a `Protocol` modelling the full composed
interface. This keeps cross-mixin dependencies explicit and passes
`mypy --strict` without runtime cost.

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
Discovery terms queue with **dynamic time-decay scoring** (Phase 2):

- `fetch_batch(batch_size)` — ordered by a **read-time score**
  `mention_count * MENTION_WEIGHT − hours_in_queue * DECAY_PER_HOUR + priority`
  (FOR UPDATE SKIP LOCKED). The score depends on `NOW()` so it is not indexable;
  the cull keeps the table small enough that the sort is negligible.
- `cull_stale(below)` — deletes terms whose score dropped below `below`
  (**opt-in**: only runs when `SEARCH_QUEUE_CULL_BELOW` is set to a number;
  `None` disables it so the queue stays a long-lived accumulator and never
  starves the hunter). **Never** culls terms mid-pagination
  (`next_page_token IS NOT NULL`). Called from the janitor's Phase 0b.
- `update_state()` — sets next_page_token, result_count after search
- `add_terms(terms)` — batch insert with dedup, increments mention_count on conflict
- `priority` is now a **manual boost** added into the score (0 by default).

The `search_queue` table has a `created_at TIMESTAMPTZ DEFAULT NOW()` column
driving the decay. Weights are configurable via `SEARCH_QUEUE_*` settings.

### 6. Vault — `vault.py`

Abstract `VaultStrategy` with two implementations:

| Operation | HF Implementation | GCS Implementation |
|---|---|---|
| `store_json(path, data)` | Upload JSON to HF dataset | Write to GCS bucket |
| `fetch_json(path)` | Download from HF dataset | Read from GCS bucket |
| `store_binary(path, data)` | Upload bytes | Write bytes |
| `store_transcript(video_id, data)` | Store transcript, returns vault URI | Same |
| `store_visual_evidence(video_id, frames)` | Upload screenshot frames | Same |
| `append_metrics(metrics_data)` | Append to Parquet on HF | Append to Parquet on GCS |
| `make_uri(path)` | `hf://datasets/<repo>/<path>` | `gs://<bucket>/<path>` |
| `list_files(prefix)` | List HF dataset files | List GCS objects |

`store_transcript()` writes the transcript payload to the vault and returns its
provider-qualified URI (via `make_uri()`); the Scribe persists that URI to the
`transcripts` table through `VideoRepository.record_transcript()`.

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

Ring sizes come from `settings.effective_pool_sizes()` — the dynamic allocation
cache if present (see `key_pool.py`), otherwise the `KEY_POOL_*_SIZE` env vars.
Keys rotate infinitely within a ring.

#### `ResiliencyExecutor`
Handles API key exhaustion with the Resiliency Strategy:

```python
executor = ResiliencyExecutor(key_ring, agent_name="hunter")
result = await executor.execute_async(make_request)
```

- On 429 (and other quota indicators): rotates key and retries
- On all keys exhausted: raises `QuotaExhaustedError`; the caller (e.g. the
  Maia agent layer) decides the resiliency action (typically restarting the
  container to rotate the egress IP). A plain exception — not `sys.exit` — is
  used so Atlas never tears down an unrelated host process that imports it.
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
| `COMPLIANCE_MODE` | `false` | Opt-in API policy enforcement; rotation/resilience always preserved |
| `JANITOR_RETENTION_DAYS` | `7` | Days before archiving videos |
| `KEY_POOL_ARCHEOLOGY_SIZE` | `1` | Fixed reserve for the archeology ring |
| `KEY_POOL_TRACKING_SIZE` | `1` | Floor for the tracking ring (scaled up dynamically) |
| `KEY_POOL_ALLOCATION_PATH` | `data/pool_allocation.json` | Cache file for the dynamic allocation |
| `DISCORD_WEBHOOK_*` | — | Webhook URLs for notification channels |

---

## Design notes

- **Stateless**: Atlas has no agent logic, no workflows, no long-running processes. It's a library.
- **No hard dependencies on optional providers**: HF/GCS imports are guarded by `try/except`. Missing provider libraries don't break core functions.
- **Type-safe**: All models use Pydantic v2 with `from_attributes=True` for DB compatibility.
- **Thread-safe vault offload**: Vault operations run via `asyncio.to_thread` to avoid blocking the event loop.
- **No ORM**: Raw SQL with repository pattern. Explicit queries for performance and control.
- **Dynamic key-pool allocation** (`key_pool.py`, `config.py`): The pool is split into `hunting`/`tracking`/`archeology` rings sized by quota economics, not a fixed split. `search.list` (hunter) costs a 100-calls/day-per-key bucket while `videos.list` (tracker) costs 1 unit, so the hunter is the natural throttle point and rate-limits first — discovery backs off before stats go stale. Sizes are recomputed weekly and cached to a JSON override so frequently-restarting agents read a stable value without hitting the DB (`config.effective_pool_sizes` consults the cache first, keeping config free of any DB dependency). `COMPLIANCE_MODE` logs a warning but never collapses the pool — doing so would disable rotation and guarantee exhaustion on the first 429.
- **Video fan-out/fan-in pipeline** (`models/video.py`, `repositories/video/state_machine.py`, `protocols.py`): Each derived artifact (raw/audio/visuals/transcript/clip, from streamer/singer/painter/scribe/muralist) has its own phase column so a video's progress reads as a state rather than a conjunction of booleans. `PROCESSED` is latched only once audio+visuals+transcript are all present, because the parallel consumers may finish out of order (audio often last). Raw reclamation uses a join-barrier: the raw is reclaimed only after the mandatory consumers (`audio` singer + `visuals` painter, and `clip` muralist unless manual-only past `RAW_TTL_HOURS`) are DONE — preventing the muralist-starvation bug. Legacy booleans remain a transitional seam kept in sync by the `sync_step_phases` trigger; `pipeline_phase` is a derived ops/monitoring frontier that never drives claim selection.
- **Janitor archive safety & HF commit-rate batching** (`repositories/video/janitor.py`): Vault writes run off the event loop via `asyncio.to_thread` (the HF/GCS SDK is synchronous). The whole sub-batch is committed to the vault in a single commit to stay within HuggingFace's 128-commits/hour limit. Marking ARCHIVED and purging the hot-tier rows happen inside one transaction, so a mid-purge failure cannot leave a video marked ARCHIVED while its stats/transcript rows remain; on vault failure the hot DB record is left untouched for retry next cycle.
- **Cold-stats archival correctness fixes** (`janitor.py`): The `FOR UPDATE` lock is released before the blocking vault write (previously held for the entire upload, blocking concurrent writers and pinning a pooled connection). The purge uses a multi-argument `unnest` form — a guaranteed zip on every PostgreSQL version; the old `SELECT unnest(a), unnest(b)` was a Cartesian product on PG < 10 and could delete unrelated rows.
- **Janitor terminal-state bug** (`janitor.py`): The previous code filtered on a `'DONE'` status literal that matched no rows, so the janitor silently deleted nothing; `PROCESSED` and `ARCHIVED` are now the eligible terminal states.
- **Transcript staging = janitor-owned persistence** (`repositories/video/transcript.py`): The Scribe only stages transcripts locally (inserts the row + optional audio bytes) and sets `vault_write_pending`; the janitor flushes staged transcripts/audio to the vault in batched commits, keeping `vault_uri` NULL until the flush succeeds. This domain was extracted into its own repository per the DAO pattern.
- **Resiliency uses a catchable exception, not `sys.exit`** (`utils.py`): `QuotaExhaustedError` is a plain `Exception` so it propagates through `asyncio` task groups and never tears down an unrelated host process that merely imports Atlas as a library; the caller (Maia agent layer) decides the resiliency action (typically restarting the container for IP rotation).
- **Quota-error classification** (`utils.py`): A bare `403` is deliberately excluded from quota detection because it usually means a private/region-blocked/age-restricted video, not quota; YouTube often returns `403` with a `"quotaExceeded"` body, handled via an explicit `http 403` guard so unrelated non-retryable videos don't trigger needless key rotation and resiliency termination.
- **`VideoRepository` mixin composition** (`repositories/video/`): The repository is assembled from focused mixins (ingestion/tracking/state/janitor/quality/transcript) for readability. Where a mixin calls a sibling's method, `self` is typed as `VideoRepositoryProtocol` (extending `DatabaseAdapterProtocol`) so `mypy --strict` passes with zero runtime cost and cross-mixin dependencies stay explicit.
- **Schema provisioning tolerates managed Postgres** (`db.py`): Extension creation and `create_hypertable` calls are best-effort (each in its own connection so a failure can't poison a shared transaction); on Neon/RDS/Crunchy without TimescaleDB/vector the schema still provisions by skipping hypertable conversion. `CREATE TABLE` statements remain mandatory.
- **Vault metrics append** (`vault.py`): Each `append_metrics` call writes its own timestamped Parquet batch file rather than reading, concatenating, and rewriting an accumulated file — this avoids lost updates from concurrent writers racing on one shared file and O(n²) rewrite overhead.
- **Cross-process agent state** (`state.py`): Quota-exhausted marks carry a TTL because quota recovers on its own (YouTube daily, HF hourly) — without it a single historical mark would be reported by the heartbeat forever; agents also clear their own mark on a successful cycle. Paid STT usage is capped daily (`SCRIBE_DAILY_AUDIO_CAP`) so once hit the scribe falls back to captions-only rather than blowing the budget.

---

## License

MIT
