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
│     │         │         │         │                  │
│     ▼         ▼         ▼         ▼                  │
│  ┌───────┐ ┌───────┐ ┌──────┐ ┌────────┐            │
│  │Scribe │ │Painter│ │Streamer┼─▶ audio/  │          │
│  │(capt.)│ │(frames)│ │(audio)│  {id}.opus│          │
│  └───────┘ └───────┘ └───┬──┘ └────┬───┘            │
│                            │        │                 │
│  ┌────────┐ ┌───────┐     │        │  vault           │
│  │Tracker │ │Janitor│     ▼        ▼                  │
│  │(stats) │ │(clean)│  ┌────────┐ ┌────────┐          │
│  └────────┘ └───────┘  │ Singer │ │ Muralist│          │
│  CONSUMERS             │(trans. │ │(archive │          │
│  (pull, process,       │ audio) │ │ video)  │          │
│   update)              └────────┘ └────────┘          │
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
├── utils.py             # looks_like_rate_limit, AdaptiveConcurrency, notify_quota_exhausted, run_in_executor
│
├── hunter/              # Producer — fresh video discovery
│   └── flow.py          #   fetch_batch → search_youtube → enrich → ingest
│
├── archeologist/        # Producer — historical video discovery
│   └── flow.py          #   hunt_history (year-month loops, category search)
│
├── scribe/              # Consumer — transcript extraction (captions only)
│   ├── flow.py          #   claim → process_transcript → store
│   ├── loader.py        #   TranscriptLoader (yt-dlp caption cascade)
│   ├── audio.py         #   AudioLoader (yt-dlp bestaudio → opus; used by singer)
│   ├── mistral.py       #   MistralTranscriber (Voxtral speech-to-text)
│   ├── grok.py          #   GrokTranscriber (Grok STT)
│   └── transcription.py #   orchestrator: captions (scribe) + audio (singer)
│
├── painter/             # Consumer — keyframe extraction
│   ├── flow.py          #   claim → process_frames → store
│   └── streamer.py      #   StealthVideoStreamer re-export (see media/streamer.py)
│
├── streamer/            # Producer — audio extraction + vault storage
│   └── flow.py          #   claim → process_audio → store audio/{id}.opus
│
├── singer/              # Consumer — audio transcription
│   └── flow.py          #   claim → process_singer (fetch audio → transcribe)
│
├── tracker/             # Consumer — stats monitoring
│   └── flow.py          #   fetch_targets → update_stats → log
│
├── janitor/             # Consumer — tiered storage cleanup
│   └── flow.py          #   sweep → handoff → archive_cold_stats → log
│
├── heartbeat/           # Reporter — fleet online-status → Discord
│   └── flow.py          #   probe services + pipeline snapshot → notify
│
├── media/               # Shared YouTube stream machinery (single yt-dlp path)
│   └── streamer.py      #   StealthVideoStreamer: extract_info/audio/video/captions
│
└── muralist/            # Consumer — full-video archival ("super painter"); manual only
    └── flow.py          #   claim → process_video → store videos/{id}.mp4
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
    "streamer": StreamerAgent,      # producer — extracts + stores audio
    "singer": SingerAgent,          # consumer — transcribes stored audio
    "heartbeat": HeartbeatAgent,
    # muralist is registered but NOT fleet-scheduled (manual-only capability):
    "muralist": MuralistAgent,
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
python -m maia heartbeat                          # post fleet status to Discord
python -m maia muralist --video-id <id> --store   # pull a source video (manual)
# ── Maintenance / corpus-quality commands (not fleet agents) ──
python -m maia quality-report                     # ingestion-quality stats
python -m maia quality-report --json              # raw JSON
python -m maia purge --dry-run                    # preview short-video purge
python -m maia purge --confirm                    # actually delete <3min videos + artifacts
python -m maia purge --min-duration 180 --confirm # threshold in seconds (default 180 = 3 min)
```

Each agent registers its own CLI arguments via `add_cli_args`. The dispatcher catches `KeyboardInterrupt` (exit 130) and generic exceptions incl. `QuotaExhaustedError` (exit 1); systemd restarts the unit on its timer.

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
| `looks_like_rate_limit(stderr)` | Detects HTTP 429 / bot-check / rate-limit patterns in yt-dlp stderr |
| `AdaptiveConcurrency` | Sliding-window failure-rate monitor; halves concurrency on sustained failures and alerts Discord |
| `notify_quota_exhausted(agent_name)` | Sends a CRITICAL Discord alert when the API key pool is exhausted |
| `run_in_executor(func)` | Decorator to offload sync functions to thread pool |
| `vault_op_with_retry(fn)` | Runs a blocking vault op off the event loop (returns its result) |

### Resiliency Strategy

Agents run as long-lived polling daemons (systemd), so there is **no container
suicide** ("Hydra Protocol" was removed). Failures are handled in-process:

- **API quota exhaustion** → `QuotaExhaustedError` (from `atlas.utils`) propagates
  to the agent entry point, `notify_quota_exhausted()` fires a Discord alert, and
  systemd restarts the unit on its timer (quota resets at midnight Pacific).
- **yt-dlp HTTP 429 (rate limit)** → the Scribe/Painter detect it via
  `looks_like_rate_limit()` and raise a dedicated `TranscriptRateLimitError` /
  `StreamRateLimitError`. The video is **released back to `PENDING`**
  (`release_to_pending()`) for a later retry — never marked done or failed — so
  no work is silently lost.
- **Concurrency** is kept low (see agent sections) because the VPS egress IP is
  flagged by YouTube; `AdaptiveConcurrency` further backs off under sustained
  failure rates.

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
fetch_batch → search_youtube → QUALITY GATE (enrich + filter) → enrich_channels → ingest_results
```

- Fetches search terms from `SearchQueueRepository`, ordered by **dynamic decay score**.
- For each topic, calls `YouTubeDataAPI/v3/search`.
- **Quality gate** (`maia/quality.py`): search snippets are enriched via
  `videos.list` (1 quota unit / 50 videos) and filtered *before* anything is
  persisted or snowballed, through four passes:

  1. **Per-video signal** — rejects Shorts (`< QUALITY_MIN_DURATION_SECONDS`),
     low traction (`< QUALITY_MIN_VIEWS_PER_HOUR`, fresh videos exempt), low
     engagement (`(likes+comments)/views < QUALITY_MIN_ENGAGEMENT_RATE`), and
     **AI-slop** (title/description/tags matched against `QUALITY_AI_DENYLIST`).
  2. **Shorts HEAD probe** — for candidates under `QUALITY_SHORTS_HEAD_MAX_DURATION`,
     a quota-free `HEAD /shorts/{id}` is issued: `200` ⇒ Short, `3xx` redirect to
     `/watch` ⇒ long-form. Tunable via `QUALITY_SHORTS_HEAD_*`.
  3. **Channel-statistics gate** — suspected AI-farm / spam channels are rejected
     via `channels.list` statistics: a subscriber floor (`QUALITY_MIN_SUBSCRIBERS`)
     and an upload-rate proxy (`QUALITY_MAX_VIDEOS_PER_DAY` =
     `videoCount / channel_age_days`).

  Only passers are ingested; only their tags seed the snowball. Disable the whole
  gate with `QUALITY_GATE_ENABLED=false`.
- Ingests passing results via `VideoRepository.ingest_video_metadata()` (now also
  stores `duration` from the enriched `contentDetails`).
- **Snowball sampling** — tags from quality-passing videos feed back into the
  search queue (dynamic-decay scored).

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

**Role**: Consumer. Fetches YouTube **captions only** and stores them in the
vault. Audio transcription is handled separately by the **Streamer** (§5, which
extracts + stores the audio) and the **Singer** (§6, which transcribes the
stored audio) — this keeps the Scribe free of STT-quota cost and YouTube rate
limits.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_scribe_targets` | `fetch_scribe_targets_task(batch_size)` | `list[Video]` — PENDING videos claimed atomically |
| `process_transcript` | `process_transcript_task(video)` | `None` — fetch captions → vault → mark transcript_safe |

**Flow**:

```
fetch_scribe_targets → process_transcript (per video, concurrent=2, 1.5s pacing)
```

```python
from maia.scribe import run_scribe_cycle

stats = await run_scribe_cycle(batch_size=10)
```

#### TranscriptLoader — `loader.py`

| Aspect | Detail |
|---|---|
| **Library** | `yt-dlp` subprocess (synchronous, offloaded to thread pool) |
| **Auth** | Netscape cookies.txt via `--cookies`, TLS impersonation via `curl_cffi` |
| **PO tokens** | `bgutil` provider (see PO Token Setup below) — required by YouTube 2026 |
| **JS Runtime** | `deno` for BotGuard/JS challenge solving |
| **Challenge solver** | `--remote-components ejs:github` fetches the EJS signature/n-sig solver |
| **Format** | JSON3 (`--write-auto-subs --sub-format json3 --sub-langs en`); output files end in `.json3` |

**Transcription strategy** — the Scribe is **captions-only**. It reads official
/ auto captions via the shared `TranscriptLoader` caption cascade (see below)
and never downloads audio:

| Mode | Behaviour |
|---|---|
| `captions` (always) | yt-dlp caption cascade only (free, but per-IP throttled) |

**Caption cascade** (`_CAPTION_CLIENTS = ["default", "tv", "mweb"]`): YouTube
throttles the per-IP `timedtext` endpoint, but player clients resolve captions
through semi-independent surfaces. A success or genuine "no subtitles"
short-circuits; `TranscriptRateLimitError` is only raised when **every** client is
throttled.

**Audio path (Streamer + Singer)** — videos with no captions are not abandoned:
the **Streamer** (§5) extracts each video's audio once and stores it at
`audio/{id}.opus`, and the **Singer** (§6) later transcribes that stored file via
the Grok → Mistral cascade (`transcription.py::transcribe_audio_path`). This
removes the ~100–200/hr caption ceiling and transcribes no-caption videos
without the Scribe ever touching STT quota. (Historical note: the Scribe used to
download audio itself; that responsibility moved to the producer/consumer pair
so every video is fetched from YouTube exactly once for its audio.)

**Error handling**:

| Exception | Action |
|---|---|
| `TranscriptExtractionError` (subtitles genuinely unavailable) | → Mark `transcript_safe` (skipped, not failed) |
| `TranscriptRateLimitError` (HTTP 429 / bot check) | → `release_to_pending()` — re-queued for retry |
| Empty subtitle file | → `TranscriptExtractionError` |

On success, the transcript is written to the vault and its URI is persisted to the
`transcripts` table via `VideoRepository.record_transcript()`.

Concurrency capped at `MAX_CONCURRENT_TRANSCRIPTS = 2` with a `SCRIBE_THROTTLE_SECONDS = 1.5`
pause between fetches (VPS IP is rate-limited by YouTube).

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
fetch_painter_targets → process_frames (per video, concurrent=2)
```

```python
from maia.painter import run_painter_cycle

stats = await run_painter_cycle(batch_size=5)
```

On HTTP 429 the streamer raises `StreamRateLimitError` and the video is
released back to `PENDING` (`release_to_pending()`) instead of being marked
FAILED. Concurrency: `MAX_CONCURRENT_VIDEOS = 2`.

#### StealthVideoStreamer — `streamer.py`

Uses `yt-dlp` via subprocess (not Python API) to avoid constructor incompatibilities with the ImpersonateTarget. Resolves stream URLs with TLS impersonation + cookie authentication.

**yt-dlp invocation**:

```
yt-dlp --quiet --no-warnings \
       --remote-components ejs:github \
       --impersonate Chrome-131 \
       --extractor-args "youtube:player_client=web_safari,web" \
       --js-runtime deno:/path/to/deno \
       --dump-json --no-download \
       --cookies <cookies.txt> \
       <video_url>
```

| Flag | Rationale |
|---|---|
| `--remote-components ejs:github` | Fetches the EJS signature / n-sig challenge solver |
| `--impersonate Chrome-131` | TLS fingerprint via `curl_cffi` — bypasses YouTube bot detection on VPS IPs |
| `--extractor-args player_client=web_safari,web` | Clients that accept PO tokens (the `android` client does **not** support cookies) |
| `--js-runtime deno:...` | Deno solves BotGuard JavaScript challenges for PO tokens |
| `--dump-json` | Outputs full info dict to stdout for Python parsing |

> **Note:** `--no-plugin-dirs` must **not** be used — it disables the `bgutil`
> PO token provider plugin, which YouTube now requires. The provider runs as a
> separate service (see PO Token Setup below).

#### PO Token Setup — `bgutil` provider

YouTube (2026) requires a per-video Proof-of-Origin (PO) token for authenticated
GVS/subtitle requests. Provided by
[`bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider):

- **Plugin**: `bgutil-ytdlp-pot-provider` installed in the venv (auto-discovered by yt-dlp).
- **Server**: a Node HTTP server on `127.0.0.1:4416`, managed by the
  `bgutil-provider` systemd unit (`~/bgutil-ytdlp-pot-provider/server/build/main.js`).
- **JS runtime**: `deno` on `PATH` (symlinked to `/usr/local/bin/deno`).
- **Verify**: `yt-dlp -v <url> | grep 'PO Token Providers'` should list
  `bgutil:http`.
- **Gotcha**: the stale `yt-dlp-get-pot` package conflicts with yt-dlp's built-in
  PO framework ("No request handlers configured") — keep it uninstalled.

Cookies (`www.youtube.cookies.txt`) must be exported from a logged-in **incognito**
session and closed without logging out, or YouTube rotates and invalidates them.

#### FFmpeg Surgical Extraction — `flow.py`

Single-frame extraction via `subprocess.Popen`:

```bash
ffmpeg -ss <timestamp> -i <stream_url> -frames:v 1 \
       -vf "scale=-2:'min(ih,720)'" -q:v 3 -f image2 -c:v mjpeg - -y -hide_banner -loglevel error
```

| Flag | Rationale |
|---|---|
| `-ss` before `-i` | Fast input-seeking via container index (not decode-from-start) |
| `-frames:v 1` | Extract exactly one frame |
| `-vf scale=-2:'min(ih,720)'` | Downscale to `FRAME_HEIGHT` (never upscale), preserve aspect |
| `-q:v 3` | JPEG quality (`FRAME_JPEG_QUALITY`, ≈ q90) |
| `-f image2 -c:v mjpeg` | Output JPEG to stdout pipe |
| `timeout 20s` | `process.communicate(timeout=20)` — kill stalled extractions |

Concurrency: `MAX_CONCURRENT_VIDEOS = 2` via `asyncio.Semaphore`. FFmpeg offloaded to thread pool via `asyncio.to_thread`.

**Frame planning** (`plan_timestamps`): a uniform grid (one frame every
`FRAME_INTERVAL_SECONDS`, default **15 s**) **combined with** chapter starts and
the top `HEATMAP_PEAKS` (default 8) "most replayed" heatmap peaks, de-duplicated
and clamped to `[MIN_FRAMES=4, MAX_FRAMES=60]` (even downsample when over, uniform
back-fill when under). `select_stream_url` picks the source stream at/just above
`FRAME_HEIGHT` to minimise download. Frame grabs cost **no API quota** — only
bandwidth/CPU/storage — so density is freely tunable via the module constants.

**Frame format**: WebP (`FRAME_FORMAT="webp"`, `FRAME_WEBP_QUALITY=80`) — ~50 %
smaller than JPEG at comparable quality (measured 20 KB vs 39 KB/frame at 720p).
Set `FRAME_FORMAT="jpg"` to fall back to MJPEG.

**Storage** (measured, 720p WebP ≈ 20 KB/frame): a 5–10 min video
(~450 s ⇒ ~31 frames) is **≈ 0.6 MB of frames** (~0.8 MB incl. transcript +
metadata). At 100k videos ≈ **80 GB**; 500k ≈ **400 GB**; 1M ≈ **800 GB**.

```bash
python -m maia painter
maia-painter
```

---

### 5. Streamer — Audio Extraction Producer

**Role**: Producer. Extracts each video's audio track **once** and archives it to
the vault at `audio/{id}.opus`. The **singer** consumer later transcribes that
stored file. Keeping the YouTube audio fetch in exactly one place (the streamer)
means every video is pulled from YouTube a single time for its speech track,
instead of the Scribe re-downloading audio per video (which triggered per-IP 429s
before).

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_streamer_targets` | `fetch_streamer_targets_task(batch_size)` | `list[Video]` — videos with `has_audio = FALSE` |
| `process_audio` | `process_audio_task(video)` | `(video_id, opus_bytes)` — extract → defer store |
| `run_streamer_cycle` | `streamer_flow(batch_size)` | batched vault write (1 commit) + `mark_audio_safe` |

**Flow**:

```
fetch_streamer_targets → process_audio (per video, concurrent=2) → store_batch (1 commit) → mark_audio_safe
```

On HTTP 429 the streamer raises `StreamRateLimitError` and the video is released
back to `PENDING` (`release_to_pending()`); on other extraction failures it is
also released (the streamer is meant to self-heal on retry, not strand videos as
`FAILED`). Concurrency: `MAX_CONCURRENT_VIDEOS = 2`.

```bash
python -m maia streamer
maia-streamer
```

### 6. Singer — Audio Transcription Consumer

**Role**: Consumer. For every video whose audio the **streamer** already stored
(`has_audio = TRUE`) but which still lacks a transcript (`has_transcript =
FALSE`), it fetches the stored `audio/{id}.opus` from the vault, transcribes it
locally via the Grok → Mistral cascade, and stages the transcript for the
janitor to flush (Option A persistence model). By consuming the *already-stored*
audio instead of re-downloading it, the singer stays decoupled from YouTube
rate limits.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_singer_targets` | `fetch_singer_targets_task(batch_size)` | `list[Video]` — `has_audio AND NOT has_transcript` |
| `process_singer` | `process_singer_task(video)` | `None` — fetch audio → transcribe → stage |
| `run_singer_cycle` | `singer_flow(batch_size)` | processes a batch |

**Flow**:

```
fetch_singer_targets → process_singer (per video, concurrent=2) → record_transcript → mark_transcript_safe
```

**Transcription** (`transcribe_audio_path` in `transcription.py`): the same
Grok→Mistral cascade the Scribe used for audio fallback, but run on a *local*
file. Strategy (`settings.SCRIBE_TRANSCRIBER`):

| Mode | Behaviour |
|---|---|
| `grok` | audio → Grok STT only |
| `mistral` | audio → Voxtral only |
| **`auto`** (default) | audio → Grok, falling back to audio → Voxtral |

**Error handling**:

| Exception | Action |
|---|---|
| `TranscriptRateLimitError` (HTTP 429) | → `release_to_pending()` — re-queued for retry |
| `TranscriptExtractionError` (no transcriber / no speech) | → `mark_transcript_safe` (skipped, not failed) |
| stored audio missing in vault | → `mark_failed` |

Concurrency: `MAX_CONCURRENT_TRANSCRIPTS = 2`, `SINGER_THROTTLE_SECONDS = 1.5`.

```bash
python -m maia singer
maia-singer
```

---

### 7. Tracker — Stats Monitoring

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

### 8. Janitor — Tiered Storage Cleanup

**Role**: Consumer. Implements the video lifecycle state machine: `PENDING → PROCESSING → PROCESSED → ARCHIVED`.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `janitor_refresh_key_pools` | `refresh_key_pools_task()` | `dict` — dynamic key-pool allocation (weekly, self-gated) |
| `janitor_cull_search_queue` | `cull_search_queue_task()` | `dict` — deletes stale/low-score search terms when `SEARCH_QUEUE_CULL_BELOW` is set (opt-in; off by default) |
| `janitor_sweep` | `sweep_phase_task(batch_size)` | `list[dict]` — PROCESSED videos eligible for archival |
| `janitor_archive_batch` | `handoff_phase_task(videos_data, dry_run)` | `dict[str, Any]` — archive results |
| `janitor_archive_stats` | `archive_cold_stats_task(retention_days=7)` | `dict[str, int]` — stats rows archived |
| `janitor_log_summary` | `log_summary_task(results)` | `None` — emits final summary event |

**Flow**:

```
refresh_key_pools → archive_cold_stats → sweep_phase → handoff_phase (per batch) → log_summary
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
| **Key pools** | Recompute dynamic hunting/tracking/archeology allocation from corpus size (weekly, self-gated) |
| **Search-queue cull** | Delete terms whose time-decayed score dropped below `SEARCH_QUEUE_CULL_BELOW` (opt-in; disabled when unset — protects in-progress paginations) |
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

### 9. Heartbeat — Fleet Online-Status Reporter

**Role**: Reporter. Publishes a periodic health summary to Discord so the
operator can confirm the system is online at a glance.

Each cycle it:
1. Probes every fleet systemd unit via `systemctl is-active` (agents + `bgutil-provider`).
2. Pulls a `VideoRepository.pipeline_snapshot()` (status counts, transcript/visual coverage, ingestion in the last hour).
3. Posts a colour-coded embed to the Discord **OPS** channel — 🟢 *Online* when all units are healthy, 🟡 *Degraded* listing any down units.

```
probe services + pipeline snapshot → notifier.send(OPS)
```

Runs as `pleiades-heartbeat.service` (systemd), one snapshot per cycle,
`RestartSec=900` (every 15 min). Requires `DISCORD_WEBHOOK_OPS` (falls back to
`DISCORD_WEBHOOK_ALERTS`).

```bash
python -m maia heartbeat
```

---

### 10. Muralist — Full-Video Archival Consumer ("Super Painter")

**Role**: Consumer. Downloads each video's **source clip** at a compact native
resolution (YouTube's own pre-encoded stream — **no local re-encode**) via the
shared `StealthVideoStreamer` and archives it to the vault at
`videos/<id>.mp4`. **Status: manual-only** — there is no `pleiades-muralist`
systemd unit and it is not part of the polling loop, but it is a proper
claim-based consumer (`claim_muralist_batch` / `mark_video_safe`) so it can be
switched on (fleet-scheduled) once storage allows.

**Prefect Tasks**:

| Task | Function | Returns |
|---|---|---|
| `fetch_muralist_targets` | `fetch_muralist_targets_task(batch_size)` | `list[Video]` — `has_video = FALSE` |
| `process_video` | `process_video_task(video, height)` | `(video_id, bytes, ext)` — download → defer store |
| `run_muralist_cycle` | `muralist_flow(batch_size, height)` | batched vault write (1 commit) + `mark_video_safe` |

```
fetch_muralist_targets → process_video (per video, concurrent=2) → store_batch (1 commit) → mark_video_safe
```

```bash
python -m maia muralist                       # archive most recent un-archived video
python -m maia muralist --batch-size 10       # archive a batch
python -m maia muralist --height 480          # choose max resolution (default 720)
```

**Why it is manual-only:** storing full source video is storage-hungry (~80-150
MB per 5-10 min clip at 720p; ~13-17 MB at 360p) and re-encoding to a modern
codec (AV1/H.265) is computationally infeasible on the current 2-core VPS
(~months of CPU for 100k videos). The muralist proves the extraction path works
end to end so the project can justify an **HF storage grant** ("storage to
hoard") and **dedicated compute** ("compute to compress") with a working
reference. Verified: pulls 720p (Rick Astley 213s → 28.6 MB) and 480p streams.

**Storage math** (native download, no re-encode):

| Resolution | ~Size / 7.5-min video | Videos per 1 TB |
|---|---|---|
| 144p | ~3.6 MB | ~290k |
| 360p | ~14 MB | ~73k |
| 480p | ~37 MB | ~27k |
| 720p | ~120 MB | ~8k |

---

## Corpus Quality & Maintenance

The quality gate stops *new* low-value videos at ingestion, but the corpus may
already contain Shorts / AI-slop. Two operators-only commands manage that.

### `quality-report` — monitor ingestion quality

Prints corpus-level statistics: total videos, Shorts proportion (`< 3 min`),
duration-bucket histogram, per-status counts, and artifact coverage
(transcripts / visuals / audio / video). Run this before a purge to see the
blast radius.

```bash
python -m maia quality-report
python -m maia quality-report --json
```

### `purge` — remove short / low-quality videos

Deletes videos shorter than `--min-duration` seconds (default **180** = 3 min),
including any artifacts they already produced (`audio/`, `frames/`, `videos/`,
`transcripts/`, `metadata/`). **Defaults to `--dry-run`**; pass `--confirm` to
actually delete. Use `--keep-artifacts` to delete only the DB rows.

```bash
python -m maia purge --dry-run                    # preview (safe)
python -m maia purge --min-duration 180 --confirm # delete <3min videos + artifacts
python -m maia purge --confirm --keep-artifacts   # DB rows only
```

**Overwrite-on-reprocess:** every vault artifact path is derived deterministically
from the video ID, and `videos.id` is the primary key (`ON CONFLICT DO UPDATE`).
So if a purged video is later rediscovered, re-ingestion recreates the row and the
agents **overwrite the same vault paths in place** — no duplicate HF writes. The
purge simply clears the stale low-quality copy first, exactly like overwriting
sectors on a disk to save write cycles.

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
| `QUALITY_GATE_ENABLED` | `true` | Hunter |
| `QUALITY_MIN_DURATION_SECONDS` | `65` | Hunter |
| `QUALITY_SHORTS_HEAD_ENABLED` | `true` | Hunter (Shorts HEAD probe) |
| `QUALITY_AI_DENYLIST` | (see `config.py`) | Hunter (AI-slop denylist) |
| `QUALITY_MIN_SUBSCRIBERS` | `50` | Hunter (channel gate) |
| `QUALITY_MAX_VIDEOS_PER_DAY` | `10.0` | Hunter (channel gate) |
| `DISCORD_WEBHOOK_ALERTS` | — | All (Discord alert on quota exhaustion / status) |

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
