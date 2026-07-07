# Maia — Stateless Agent Layer

**Version 0.1.0**

Maia is the Prefect-based worker fleet for Project Pleiades. It is **stateless**: all persistence, configuration, and domain logic lives in [Atlas](../atlas). Maia agents implement the Producer-Consumer pipeline: producers discover YouTube videos, consumers extract metadata, transcripts, frames, and metrics.

```
YouTube API
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  PRODUCERS (identify targets, push to work queue)    │
│  ┌──────────┐  ┌──────────────┐                      │
│  │  Hunter  │  │ Archeologist │                      │
│  │ (fresh)  │  │ (historical) │                      │
│  └────┬─────┘  └──────┬───────┘                      │
│       │               │                              │
│       └───────┬───────┘                              │
│               ▼                                      │
│        videos table (work queue)                     │
│               │                                      │
│       ┌───────┼───────────┬──────────┐               │
│       ▼       ▼           ▼          ▼               │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐         │
│  │ Scribe │ │ Painter│ │Tracker │ │Janitor │         │
│  │(trans.)│ │(frames)│ │(stats) │ │(clean) │         │
│  └────────┘ └────────┘ └────────┘ └────────┘         │
│  CONSUMERS (pull from queue, process, update)        │
└──────────────────────────────────────────────────────┘
```

---

## Architecture

### Layers

```
maia/
├── __main__.py          # CLI dispatcher (dynamic subparsers from registry)
├── agent.py             # Agent protocol (structural contract)
├── registry.py          # AGENT_REGISTRY (name → class mapping)
├── strategies.py        # YouTubeSearchStrategy (shared HTTP/KMS logic)
├── utils.py             # RateLimitError, execute_with_rate_limit, run_in_executor
│
├── hunter/              # Producer — fresh video discovery
│   └── flow.py          #   fetch_batch → search_youtube → enrich → ingest
│
├── archeologist/        # Producer — historical video discovery
│   └── flow.py          #   hunt_history (year-month loops, category search)
│
├── scribe/              # Consumer — transcript extraction
│   ├── flow.py          #   claim → process_transcript → store
│   └── loader.py        #   TranscriptLoader (cookie auth, language fallback)
│
├── painter/             # Consumer — keyframe extraction
│   ├── flow.py          #   claim → process_frames → store
│   └── streamer.py      #   StealthVideoStreamer (yt-dlp wrapper, strategy chain)
│
├── tracker/             # Consumer — stats monitoring
│   └── flow.py          #   fetch_targets → update_stats → log
│
└── janitor/             # Consumer — tiered storage cleanup
    └── flow.py          #   sweep → handoff → archive_cold_stats → log
```

### Design Patterns

| Pattern | Location |
|---|---|
| **Agent Protocol** | `agent.py` — `Agent` runtime-checkable protocol |
| **Registry** | `registry.py` — `AGENT_REGISTRY` dict maps names to classes |
| **Strategy** | `strategies.py` — `YouTubeSearchStrategy` shared by Hunter, Archeologist, Tracker |
| **Producer-Consumer** | Hunters/Archeologist → video table → Scribes/Painters/Trackers/Janitor |
| **State Machine** | `janitor/flow.py` — PENDING → PROCESSING → PROCESSED → ARCHIVED |
| **Singleton** | Atlas pattern: `settings`, `db`, `events`, `notifier` imported from atlas |
| **Repository** | All DB access through `atlas.repositories.*` — agents never write raw SQL |
| **Retry** | Tenacity decorators on vault ops, transcript fetches, yt-dlp extraction |

---

## Shared Infrastructure

### Agent Protocol — `agent.py`

Every agent satisfies a structural contract:

```python
class Agent(Protocol):
    name: str
    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None: ...
    async def run(self, **kwargs) -> dict[str, Any]: ...
```

`@runtime_checkable` enables `isinstance(agent, Agent)` for dispatch validation.

### Agent Registry — `registry.py`

```python
AGENT_REGISTRY: dict[str, type[Agent]] = {
    "hunter": HunterAgent,
    "tracker": TrackerAgent,
    "janitor": JanitorAgent,
    "archeologist": ArcheologistAgent,
    "scribe": ScribeAgent,
    "painter": PainterAgent,
}
```

### CLI Dispatcher — `__main__.py`

```bash
python -m maia hunter --batch-size 10
python -m maia tracker
python -m maia janitor --dry-run
python -m maia archeologist --start-year 2010 --end-year 2020
python -m maia scribe
python -m maia painter
```

Each agent registers its own CLI arguments via `add_cli_args`. The dispatcher catches `RateLimitError` (exit 42), `KeyboardInterrupt` (exit 1), and generic exceptions (exit 1).

### YouTube Search Strategy — `strategies.py`

Shared by Hunter, Archeologist, and Tracker:

```python
from maia.strategies import YouTubeSearchStrategy

strategy = YouTubeSearchStrategy(key_ring_pool="hunting", agent_name="hunter")
result = await strategy.search({"q": "music", "maxResults": 50})
stats = await strategy.fetch_videos(["VIDEO_ID_1", "VIDEO_ID_2"])
```

Encapsulates:
- **Key rotation**: Uses Atlas `KeyRing` to cycle through API keys
- **Rate-limit handling**: 403/429 → retry with next key; all keys exhausted → Resiliency Strategy
- **HTTP transport**: `aiohttp.ClientSession` per request

### Utilities — `utils.py`

| Function | Purpose |
|---|---|
| `RateLimitError` | Exception class for 429 container suicide |
| `execute_with_rate_limit(executor, request_func)` | Wraps `ResiliencyExecutor`, converts `SystemExit` to `RateLimitError` |
| `run_in_executor(func)` | Decorator to offload sync functions to thread pool |
| `vault_op_with_retry(fn)` | Retries vault operations 3× with exponential backoff (2s–10s) |

### Resiliency Strategy (Hydra Protocol)

On YouTube 429 rate-limit exhaustion:
1. Rotate API keys until pool is empty
2. Logger emits `RESILIENCY: All keys exhausted for {agent_name}`
3. `sys.exit(0)` — container suicide, orchestration restarts with fresh IP

This is the **single most important safety mechanism** in Maia. It prevents silent data loss when API quotas are exhausted.

---

## Agents

### 1. Hunter — Fresh Video Discovery

**Role**: Producer. Searches YouTube for new videos matching tracked topics.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_batch` | `fetch_batch_task(batch_size)` | `list[dict]` — search terms from `SearchQueueRepository` |
| `search_youtube` | `search_youtube_task(topic, strategy)` | `dict \| None` — YouTube API search response |
| `enrich_channels` | `enrich_channels_task(channel_ids, strategy)` | `int` — channels refreshed |
| `ingest_results` | `ingest_results_task(topic, response, strategy)` | `None` — vault store + DB upsert + snowball tags |

**Flow**:

```
fetch_batch → search_youtube (per topic) → enrich_channels → ingest_results
```

- Fetches pending search terms from `SearchQueueRepository`
- For each topic, calls `YouTubeDataAPI/v3/search`
- Extracts new channel IDs and enriches them (`lookup_channels`)
- Ingests results via `VideoRepository.ingest_video_metadata()`
- Implements **snowball sampling** — discovered channel tags feed back into search terms

```python
from maia.hunter import run_hunter_cycle

stats = await run_hunter_cycle(batch_size=10)
# Returns: {"fetched": 5, "results": 42, "channels_refreshed": 3}
```

```bash
python -m maia hunter --batch-size 10
maia-hunter  # CLI entry point
```

---

### 2. Archeologist — Historical Discovery

**Role**: Producer. Discovers top-performing videos from past years (2005–2024).

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `hunt_history` | `hunt_history_task(year, month, strategy)` | `None` — searches YouTube by category and viewCount |

**Flow**:

```
run_archeology_campaign (start_year → end_year)
  └── hunt_history (per year, per month, per category)
```

Target categories: `["10" (Music), "20" (Gaming), "24" (Entertainment), "28" (Science), "27" (Education)]`

- Iterates year-by-year, month-by-month
- Searches YouTube for top videos by `viewCount` in each target category
- Ingests with `priority_override=100` (high priority for historical content)
- Reuses `enrich_channels_task` from Hunter

```python
from maia.archeologist import run_archeology_campaign

stats = await run_archeology_campaign(start_year=2010, end_year=2020)
```

```bash
python -m maia archeologist --start-year 2010 --end-year 2020
```

---

### 3. Scribe — Transcript Extraction

**Role**: Consumer. Fetches YouTube captions and stores them in the vault.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_scribe_targets` | `fetch_scribe_targets_task(batch_size)` | `list[Video]` — PENDING videos claimed atomically |
| `process_transcript` | `process_transcript_task(video)` | `None` — fetch captions → vault → mark transcript_safe |

**Flow**:

```
fetch_scribe_targets → process_transcript (per video, concurrent=5)
```

```python
from maia.scribe import run_scribe_cycle

stats = await run_scribe_cycle(batch_size=10)
```

#### TranscriptLoader — `loader.py`

| Aspect | Detail |
|---|---|
| **Library** | `youtube-transcript-api` 1.x (synchronous, offloaded to thread pool) |
| **Auth** | `requests.Session` with YouTube cookies from atlas config |
| **User-Agent** | Chrome 124 (anti-bot) |
| **Cookie format** | Netscape cookies.txt loaded via `MozillaCookieJar` |

**Language fallback chain** (for each video):
1. Manually created English captions
2. Auto-generated English captions
3. Manually created captions: es → fr → de → pt → ru → ja → ko

**Error handling**:

| Exception | Action |
|---|---|
| `IpBlocked` / `RequestBlocked` | → `RateLimitError` (container suicide) |
| `TranscriptsDisabled` | → Mark `transcript_safe` (skipped, not failed) |
| `ConnectionError` / `TimeoutError` | → Retry 3× with exp. backoff (2s–10s) |
| Empty transcript | → `TranscriptExtractionError` |

Concurrency capped at `MAX_CONCURRENT_TRANSCRIPTS = 5` via `asyncio.Semaphore`.

```bash
python -m maia scribe
maia-scribe
```

---

### 4. Painter — Keyframe Extraction

**Role**: Consumer. Extracts visual keyframes from videos using FFmpeg.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_painter_targets` | `fetch_painter_targets_task(batch_size)` | `list[Video]` — PENDING videos claimed atomically |
| `process_frames` | `process_frames_task(video)` | `None` — extract keyframes → vault → mark visuals_safe |

**Flow**:

```
fetch_painter_targets → process_frames (per video, concurrent=5)
```

```python
from maia.painter import run_painter_cycle

stats = await run_painter_cycle(batch_size=5)
```

#### StealthVideoStreamer — `streamer.py`

Uses `yt-dlp` as a Python library (not subprocess). Resolves stream URLs with cookie authentication.

**Strategy chain** (`extract_info`):

| Attempt | `player_client` | Notes |
|---|---|---|
| 1 (Primary) | `["web", "android"]` | Full YouTube web extraction |
| 2 (Fallback) | `["tv", "ios", "android"]` | TV/mobile clients, less bot detection |

**yt-dlp options**:

| Option | Value | Rationale |
|---|---|---|
| `format` | `best[ext=mp4]/best` | Progressive mp4 for HTTP Range-seeking |
| `extractor_retries` | `3` | Retry on transient failures |
| `socket_timeout` | `5` | Fail fast on unresponsive connections |
| `force_ipv4` | `true` | Avoid IPv6 routing issues |
| `geo_bypass` | `true` | Bypass geo-restrictions |
| `cookiefile` | from atlas config | Authenticated extraction |

#### FFmpeg Surgical Extraction — `flow.py`

Single-frame extraction via `subprocess.Popen`:

```bash
ffmpeg -ss <timestamp> -i <stream_url> -frames:v 1 -f image2 -c:v mjpeg - -y -hide_banner -loglevel error
```

| Flag | Rationale |
|---|---|
| `-ss` before `-i` | Fast input-seeking via container index (not decode-from-start) |
| `-frames:v 1` | Extract exactly one frame |
| `-f image2 -c:v mjpeg` | Output JPEG to stdout pipe |
| `timeout 20s` | `process.communicate(timeout=20)` — kill stalled extractions |

Concurrency: `MAX_CONCURRENT_VIDEOS = 5` via `asyncio.Semaphore`. FFmpeg offloaded to thread pool via `asyncio.to_thread`.

**Timestamps**: Extracts frames at 33%, 50%, and 66% of video duration. Falls back to linear scaling if heatmap data is unavailable.

```bash
python -m maia painter
maia-painter
```

---

### 5. Tracker — Stats Monitoring

**Role**: Consumer. Monitors view/like/comment counts for tracked videos.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_targets` | `fetch_targets_task(batch_size)` | `list[dict]` — videos needing stats updates |
| `update_stats` | `update_stats_task(videos, strategy)` | `int` — videos updated |

**Flow**:

```
fetch_targets → update_stats (batch)
```

- Fetches stale videos from `WatchlistRepository` (Adaptive Scheduling: HOURLY/DAILY/WEEKLY)
- Uses `YouTubeSearchStrategy("tracking")` to call `videos?part=statistics`
- Logs stats via `VideoRepository.log_stats_batch()`
- Caps `batch_size` at 50 (YouTube API limit)

```python
from maia.tracker import run_tracker_cycle

stats = await run_tracker_cycle(batch_size=50)
# Returns: {"fetched": 50, "updated": 50}
```

```bash
python -m maia tracker
maia-tracker
```

---

### 6. Janitor — Tiered Storage Cleanup

**Role**: Consumer. Implements the video lifecycle state machine: `PENDING → PROCESSING → PROCESSED → ARCHIVED`.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `janitor_sweep` | `sweep_phase_task(batch_size)` | `list[dict]` — PROCESSED videos eligible for archival |
| `janitor_archive_batch` | `handoff_phase_task(videos_data, dry_run)` | `dict[str, Any]` — archive results |
| `janitor_archive_stats` | `archive_cold_stats_task(retention_days=7)` | `dict[str, int]` — stats rows archived |
| `janitor_log_summary` | `log_summary_task(results)` | `None` — emits final summary event |

**Flow**:

```
sweep_phase → handoff_phase (per batch) → archive_cold_stats → log_summary
```

**State Machine**:

```
PENDING ──► PROCESSING ──► PROCESSED ──► ARCHIVED
    │                                            ▲
    └──► FAILED                                  │
                                          Janitor hand-off:
                                          1. Serialize to vault
                                          2. Verify checksum
                                          3. Purge from hot DB
```

**Phases**:

| Phase | Action |
|---|---|
| **Sweep** | `SELECT ... WHERE status='PROCESSED' AND last_updated_at < retention` |
| **Hand-off** | Serialize video metadata + stats → vault → verify → DELETE from hot DB |
| **Cold stats** | Move `video_stats_log` rows older than 7 days to vault Parquet |
| **Summary** | Emit `janitor.batch_complete` event with archivals/failures |

**Dry-run mode**: `--dry-run` flag logs what would happen without doing it.

```python
from maia.janitor import janitor_cycle

results = await janitor_cycle(dry_run=False, archive_stats=True, batch_size=50)
```

```bash
python -m maia janitor --dry-run          # preview only
python -m maia janitor --no-dry-run       # actual archival
maia-janitor
```

---

## Testing

```bash
# Unit tests (all agents mocked)
cd maia && pytest tests/ -v

# 48 tests covering:
#   hunter/       — batch fetch, YouTube search, ingestion, vault failures
#   tracker/      — target fetch, stats update, API errors, rate limits
#   scribe/       — claim, process, retries, fallbacks, vault failures
#   painter/      — claim, process, vault failures, resiliency propagation
#   janitor/      — sweep, hand-off, stats archival, vault failures
#   archeologist/ — campaign, resiliency, real API rate-limit detection
```

---

## Configuration

Maia reads all config through Atlas `settings`. Key overrides:

| Variable | Default | Used By |
|---|---|---|
| `HUNTER_BATCH_SIZE` | `10` | Hunter |
| `PAINTER_BATCH_SIZE` | `3` | Painter |
| `SCRIBE_BATCH_SIZE` | `5` | Scribe |
| `TRACKER_BATCH_SIZE` | `50` | Tracker |
| `JANITOR_ENABLED` | `true` | Janitor |
| `JANITOR_RETENTION_DAYS` | `7` | Janitor |
| `JANITOR_SAFETY_CHECK` | `true` | Janitor |
| `ALERT_ON_HYDRA_PROTOCOL` | `true` | All (Discord alert on container suicide) |

---

## Dependency Map

```
maia/
  strategies.py ───► atlas.utils (KeyRing, ResiliencyExecutor)
  hunter/flow.py ──► atlas.repositories (SearchQueueRepository, VideoRepository)
  tracker/flow.py ─► atlas.repositories (VideoRepository, WatchlistRepository)
  scribe/flow.py ──► atlas.repositories (VideoRepository) + atlas.vault
  painter/flow.py ─► atlas.repositories (VideoRepository) + atlas.vault + yt-dlp + FFmpeg
  janitor/flow.py ─► atlas.repositories (VideoRepository) + atlas.vault + atlas.events
  utils.py ────────► atlas.utils (ResiliencyExecutor)
```

---

## License

MIT
