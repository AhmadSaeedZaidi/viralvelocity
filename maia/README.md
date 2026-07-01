# Maia — Stateless Agent Layer

Maia is the Prefect-based worker fleet for Project Pleiades. It is **stateless**: all persistence, configuration, and domain logic lives in [Atlas](https://github.com/your-org/pleiades/tree/main/atlas).

## Design Patterns

### Producer-Consumer Pipeline

```
YouTube API
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  PRODUCERS (identify targets, push to work queue)    │
│  ┌──────────┐  ┌──────────────┐                     │
│  │  Hunter  │  │ Archeologist │                     │
│  │ (fresh)  │  │ (historical) │                     │
│  └────┬─────┘  └──────┬───────┘                     │
│       │               │                             │
│       └───────┬───────┘                             │
│               ▼                                     │
│        videos table (work queue)                    │
│               │                                     │
│       ┌───────┼───────────┬──────────┐              │
│       ▼       ▼           ▼          ▼              │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐      │
│  │ Scribe │ │ Painter│ │Tracker │ │Janitor │      │
│  │(trans.)│ │(frames)│ │(stats) │ │(clean) │      │
│  └────────┘ └────────┘ └────────┘ └────────┘      │
│  CONSUMERS (pull from queue, process, update)       │
└─────────────────────────────────────────────────────┘
```

### Strategy Pattern (YouTube Data API)

`YouTubeSearchStrategy` (`maia/strategies.py`) encapsulates HTTP request, key rotation, and rate-limit handling. Shared by Hunter, Archeologist, and Tracker — no duplicated `aiohttp` boilerplate.

### Repository Pattern (Data Access)

All database access goes through Atlas repositories. Maia never writes raw SQL.

```
maia/                     atlas/
  strategies.py  ─────►    utils.py (KeyRing, ResiliencyExecutor)
  hunter/flow.py ─────►    repositories/VideoRepository
  tracker/flow.py ─────►    repositories/VideoRepository
  scribe/flow.py  ─────►    repositories/VideoRepository
```

## Agents

| Agent | Role | Pattern |
|-------|------|---------|
| **Hunter** | Discover fresh videos via search queries | Producer |
| **Archeologist** | Discover historical videos by category/date | Producer |
| **Scribe** | Fetch and store YouTube transcripts | Consumer |
| **Painter** | Extract keyframes via FFmpeg | Consumer |
| **Tracker** | Monitor view/like/comment stats (3-Zone Defense) | Consumer |
| **Janitor** | Archive old stats, clean up stale videos | Consumer |

## Quick Start

```bash
cd maia
pip install -e .
cp ENV.example .env   # edit with your credentials
maia-hunter           # run hunter directly
python -m maia hunter # or via CLI dispatcher
```

## Tests

```bash
pytest tests/          # 39 unit tests
```

## Tooling & Libraries

### yt-dlp — Video Metadata & Stream URL Extraction (Painter)

Used as a **Python library** (`yt_dlp.YoutubeDL`), not a subprocess. Extracts stream URLs, chapters, and heatmap data for keyframe selection.

**Key options** (`maia/src/maia/painter/streamer.py:_get_base_options()`):
| Option | Value | Rationale |
|--------|-------|-----------|
| `format` | `best[ext=mp4]/best` | Progressive mp4 for HTTP Range-seeking via FFmpeg (not DASH) |
| `extractor_retries` | `3` | Retry on transient YouTube failures |
| `socket_timeout` | `5` | Fail fast on unresponsive connections |
| `force_ipv4` | `true` | Avoid IPv6 routing issues |
| `geo_bypass` | `true` | Bypass geo-restrictions |
| `cookiefile` | resolved from `atlas.config.settings` | Authenticated extraction via Netscape cookies.txt |

**Strategy fallback chain** (`extract_info`):
1. **Primary** (`player_client: ["web", "android"]`) — full YouTube web extraction
2. **Fallback** (`player_client: ["tv", "ios", "android"]`) — TV/mobile clients, less prone to bot detection

**Rate-limit handling**: 429 errors are immediately re-raised (not retried) to trigger container-level IP rotation via `SystemExit`. All other `DownloadError` exceptions fall through to the next strategy.

### ffmpeg — Surgical Keyframe Extraction (Painter)

Invoked via **`subprocess.Popen`** for single-frame extraction from remote streams. Much faster than OpenCV for streaming because FFmpeg uses HTTP Range requests — only downloads bytes needed for the target frame.

**Command** (`maia/src/maia/painter/flow.py:_ffmpeg_extract_frame()`):
```bash
ffmpeg -ss <timestamp> -i <stream_url> -frames:v 1 -f image2 -c:v mjpeg - -y -hide_banner -loglevel error
```

| Flag | Value | Rationale |
|------|-------|-----------|
| `-ss` | before `-i` | Fast input-seeking via container index (not decode-from-start) |
| `-frames:v` | `1` | Extract exactly one frame |
| `-f image2 -c:v mjpeg` | — | Output JPEG to stdout pipe |
| `-` | output file | Write to pipe (captured via `stdout=subprocess.PIPE`) |
| `timeout` | `20s` | `process.communicate(timeout=20)` — kill stalled extractions |

Concurrency: limited to `MAX_CONCURRENT_VIDEOS=5` via `asyncio.Semaphore`. FFmpeg work is offloaded to a thread pool via `asyncio.to_thread()`.

### youtube-transcript-api — Caption Fetching (Scribe)

The Scribe uses `youtube-transcript-api` 1.x as a Python library. **faster-whisper is not used** — transcripts come from YouTube's existing caption tracks, which is faster and cheaper than local ASR.

**Cookies** (`maia/src/maia/scribe/loader.py:_build_http_client()`):
- Builds a `requests.Session` with YouTube cookies from `atlas.config.settings.youtube_cookies_resolved_path`
- Sets a Chrome 124 `User-Agent` header for anti-bot measures
- Cookies are loaded via `MozillaCookieJar.load(ignore_discard=True, ignore_expires=True)`

**Language fallback chain** (`TranscriptLoader.fetch()`):
1. Manually created English captions
2. Auto-generated English captions
3. Manually created captions in other languages (es, fr, de, pt, ru, ja, ko)

**Error handling**:
| Exception | Action |
|-----------|--------|
| `IpBlocked` / `RequestBlocked` | → `RateLimitError` (triggers container suicide) |
| `TranscriptsDisabled` | → Mark video as `transcript_safe` (skipped, not failed) |
| `ConnectionError` / `TimeoutError` | → Retry up to 3× with exp. backoff (2-10s) |

Concurrency: bounded to `MAX_CONCURRENT_TRANSCRIPTS=5` via `asyncio.Semaphore`. The `loader.fetch()` call is offloaded to a thread executor since `youtube-transcript-api` is synchronous.

## Resiliency Strategy

On YouTube 429 rate limit, Maia terminates immediately via `SystemExit` (container suicide for IP rotation). See `maia/utils.py` → `execute_with_rate_limit()`.
