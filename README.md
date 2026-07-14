# Pleiades

**Viral video intelligence platform for YouTube content discovery and tracking at scale**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## Overview

Pleiades is a high-throughput video intelligence platform that:

- 🔍 **Discovers** viral content through intelligent YouTube search
- 📊 **Tracks** video metrics forever with minimal SQL footprint (<0.5 GB)
- 🚀 **Scales** to 100k+ videos/day using an Adaptive Scheduling architecture
- 🔑 **Manages** API quotas intelligently via a Resiliency Strategy
- 🗄️ **Stores** time-series data efficiently in a HuggingFace / GCS vault

The system is a **producer/consumer pipeline** orchestrated by [Prefect](https://www.prefect.io/)
across two machines (see [Deployment](docs/deploy.md)): a tiny Oracle **control
plane** running the Prefect server, and a **2-core executor VPS** that runs the
agent fleet, the production Postgres DB, and the HuggingFace vault.

---

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Neon serverless)
- HuggingFace account or Google Cloud Storage
- YouTube Data API v3 keys
- **Deno** ≥ 2.3.0 (required by `yt-dlp` for YouTube's BotGuard / PoToken challenge)
- **FFmpeg** (frame extraction + audio chunking)
- A **bgutil Po-token provider** server (see [Deployment §4.2](docs/deploy.md))

### Installation

```bash
git clone <repo> pleiades
cd pleiades

# Set up virtual environment
python -m venv .venv
source .venv/bin/activate

# Install components (atlas = lib, maia = agents)
make install

# Copy environment templates and fill in credentials (NEVER commit .env)
cp .env.example .env
cp atlas/ENV.example atlas/.env
cp maia/ENV.example maia/.env
```

### Initialize the database

```bash
cd atlas && make setup      # provisions schema.sql
```

### Run an agent manually (development)

```bash
cd maia && python -m maia hunter --batch-size 10
python -m maia painter
python -m maia heartbeat
```

In production the agents run as **Prefect deployments** (9 of them) scheduled by the
control plane — see [Deployment](docs/deploy.md) and the
[orchestration runbook](docs/micro-prefect-orchestration.md).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE (micro)                          │
│  Prefect server/API :4200  (SQLite orchestration DB — deployments,     │
│  schedules, flow-run state. No video data.)                            │
└───────────────────────────────────┬──────────────────────────────────┘
                                      │ PREFECT_API_URL
                                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       EXECUTOR VPS (2-core)                            │
│                                                                        │
│  prefect-worker  ──►  default work pool (concurrency_limit = 9)        │
│       │                                                                │
│       ├─ streamer  (producer)  ─┐                                      │
│       ├─ hunter / archeologist (producers)                             │
│       ├─ singer (audio→transcript)  ─┐  fan-out from raw               │
│       ├─ painter (frames)          ─┤                                  │
│       ├─ scribe (captions)         ─┘  (parallel consumers)           │
│       ├─ tracker (stats)                                             │
│       ├─ janitor (tiered storage)                                    │
│       └─ heartbeat (fleet status → Discord)                           │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  MAIA  (stateless agent layer — all logic)                    │     │
│  │     producers → videos table (work queue) → consumers         │     │
│  └───────────────────────────────┬─────────────────────────────┘     │
│                                   ▼                                   │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  ATLAS  (infrastructure library — plumbing only)              │     │
│  │   • PostgreSQL  — video DB (hot tier, tiered storage)         │     │
│  │   • Vault       — HF/GCS (cold tier: frames/audio/transcripts)│     │
│  │   • Repositories — VideoRepository, ChannelRepository, …      │     │
│  │   • Events / Notifications / KeyRing / Resiliency             │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                                                                        │
│  alkyone — integration test suite (isolated infra only, never prod)    │
└──────────────────────────────────────────────────────────────────────┘
```

**Pipeline shape (fan-out / fan-in).** After a video is fetched, three consumers
derive from the single `raw` artifact **in parallel** — `singer` (audio),
`painter` (visuals), `muralist` (clip) — while `scribe` (transcript) runs
independently (caption-first). Each artifact has its own phase column
(`raw/audio/visuals/transcript/clip`); `PROCESSED` is latched only once
audio + visuals + transcript are all present. `raw` is reclaimed (disk-bounded)
only after its mandatory consumers are DONE or the TTL window expires. See
[`docs/agent-consolidation-proposal.md`](docs/agent-consolidation-proposal.md).

**See [Architecture Guide](docs/architecture.md) for complete design.**

---

## Key Features

### 🎯 Adaptive Scheduling

Track videos **forever** while keeping SQL under 0.5 GB:

- Lightweight `watchlist` table for scheduling (≈50 bytes/video)
- Heavy metrics stored in Parquet files in the vault (unlimited)
- Adaptive tracking tiers (HOURLY → DAILY → WEEKLY)
- Survives video cleanup (tracking continues after deletion)

**[Learn more →](docs/adaptive-scheduling.md)**

### 🔑 Resiliency Strategy

Intelligent API key management:

- Automatic key rotation on quota exhaustion (`KeyRing` + `ResiliencyExecutor`)
- `QuotaExhaustedError` propagates and triggers a Discord alert (no `sys.exit`)
- Rate-limit errors release the video back to `PENDING` for retry — never `FAILED`

**[Learn more →](docs/resiliency-strategy.md)**

### ⚡ Tiered Storage Architecture

Ephemeral data management for high throughput:

- Videos auto-archived to the vault after `JANITOR_RETENTION_DAYS` (default 7)
- SQL footprint stays constant (<0.5 GB)
- Fast queries on recent data only
- 100k+ videos/day ingestion capacity

**[Learn more →](docs/tiered-storage.md)**

---

## Documentation

### Getting Started
- **[Quick Start](docs/quickstart.md)** - Get up and running
- **[Architecture Overview](docs/architecture.md)** - System design and components
- **[Deployment (two-VPS)](docs/deploy.md)** - Control plane + executor setup
- **[Orchestration Runbook](docs/micro-prefect-orchestration.md)** - Ops & incidents

### Core Features
- **[Adaptive Scheduling](docs/adaptive-scheduling.md)** - Infinite video tracking
- **[Resiliency Strategy](docs/resiliency-strategy.md)** - API key management
- **[Tiered Storage](docs/tiered-storage.md)** - Ephemeral data management

### Development
- **[Testing Guide](docs/testing.md)** - Unit, integration, and smoke testing
- **[Contributing](docs/contributing.md)** - Development workflow and standards
- **[Challenges Log](docs/CHALLENGES.md)** - Problems we hit and how we solved them

### Component Guides
- **[Atlas](atlas/README.md)** - Infrastructure layer (DB, Vault, Events, Notifications)
- **[Maia](maia/README.md)** - Agent fleet (Hunter, Tracker, Scribe, Painter, …)
- **[Alkyone](alkyone/README.md)** - Integration testing (isolated infra only)

---

## Project Structure

```
pleiades/
├── docs/                    # 📚 Unified documentation
│   ├── README.md            # Documentation index
│   ├── deploy.md            # Two-VPS deployment guide
│   ├── micro-prefect-orchestration.md  # Ops runbook
│   ├── challenges.md        # Engineering challenges log
│   ├── architecture.md      # System design
│   ├── adaptive-scheduling.md
│   ├── resiliency-strategy.md
│   ├── tiered-storage.md
│   ├── testing.md
│   └── contributing.md
│
├── atlas/                   # 🏗️ Infrastructure library (no agent logic)
│   ├── src/atlas/
│   │   ├── db.py            # PostgreSQL connection pool
│   │   ├── vault.py         # HF/GCS storage
│   │   ├── events.py        # Event bus
│   │   ├── notifier.py      # Discord alerts
│   │   ├── utils.py         # KeyRing, ResiliencyExecutor
│   │   ├── config.py        # Pydantic settings (env-driven)
│   │   ├── schema.sql       # Database schema
│   │   ├── models/          # Pydantic domain models
│   │   └── repositories/    # VideoRepository, ChannelRepository, … (Repository pattern)
│   └── tests/
│
├── maia/                    # 🤖 Stateless agent fleet (Prefect flows)
│   ├── src/maia/
│   │   ├── hunter/          # Discovery (producer)
│   │   ├── archeologist/    # Historical discovery (producer)
│   │   ├── tracker/         # Stats monitoring (consumer)
│   │   ├── janitor/         # Tiered-storage cleanup (consumer)
│   │   ├── painter/         # Keyframe extraction (consumer)
│   │   ├── scribe/          # Captions extraction (consumer)
│   │   ├── streamer/        # Audio extraction + vault storage (producer)
│   │   ├── singer/          # Audio transcription (consumer)
│   │   ├── muralist/        # Full-video archival (consumer, manual-only)
│   │   ├── media/           # Shared yt-dlp streamer (single path)
│   │   └── heartbeat/       # Fleet status reporter
│   └── tests/
│
├── alkyone/                 # 🧪 Integration testing (isolated infra only)
└── tools/                   # Orchestration + recovery scripts (no secrets)
```

---

## Usage Examples

### Discovery (Hunter)

```python
from maia.hunter import run_hunter_cycle

stats = await run_hunter_cycle(batch_size=10)
# Discovers videos, adds to watchlist (Adaptive Scheduling)
```

### Monitoring (Tracker)

```python
from maia.tracker import run_tracker_cycle

stats = await run_tracker_cycle(batch_size=50)
# Fetches from watchlist, stores stats to the vault
```

### Data Access (Repository pattern)

```python
from atlas.repositories import VideoRepository

repo = VideoRepository()
await repo.ingest_video_metadata(video_data)          # upsert a video
batch = await repo.claim_scribe_batch(batch_size=10)  # atomic claim
await repo.mark_transcript_safe(video.id)             # advance state
```

All DB access goes through `atlas.repositories.*` — agents never write raw SQL.
See [Atlas README](atlas/README.md) for the full repository surface.

---

## Performance

- **Throughput**: 100k+ videos/day
- **SQL Footprint**: <0.5 GB (constant)
- **Tracking Duration**: Infinite (Adaptive Scheduling)
- **API Efficiency**: Multi-key rotation (Resiliency Strategy)
- **Storage**: Unlimited (compressed Parquet / WebP in the vault)

---

## Testing

```bash
# Unit tests (atlas + maia, all mocked)
make test-unit

# Integration tests (alkyone — isolated infra only, never production)
make test-int

# Everything
make test
```

**See [Testing Guide](docs/testing.md) for detailed instructions.**

---

## Deployment

Pleiades runs as **9 Prefect deployments** across two VPSes (control plane +
executor). See the dedicated guides:

- **[Deployment (two-VPS)](docs/deploy.md)** — provisioning both machines, systemd
  units, the 9 deployments + work queues, and secrets handling (no keys in repo).
- **[Orchestration Runbook](docs/micro-prefect-orchestration.md)** — everyday ops,
  concurrency model, and incident playbooks.

A `Dockerfile` also exists for single-image builds (atlas + maia in one image);
it is useful for isolated dev but the production topology is the two-VPS Prefect
deployment above.

> **Requirements for the executor**
> - **Deno** (≥ 2.3.0) — required by `yt-dlp` to solve YouTube's BotGuard/PoToken
>   challenge.
> - A **bgutil Po-token provider** server (`bgutil-provider` systemd unit) — YouTube
>   (2026) requires a per-video Proof-of-Origin token. See `maia/README.md`
>   (PO Token Setup).
> - Vault writes are persisted to `./data/vault` (mounted into every agent).
> - For authenticated/PoToken sessions, mount a cookies file and set
>   `YOUTUBE_COOKIES_PATH` (Atlas reads that var, not `MAIA_COOKIES_PATH`).

---

## Contributing

We welcome contributions! Please read our [Contributing Guide](docs/contributing.md) for:

- Development setup
- Coding standards
- Testing requirements
- Pull request process

### Quick Contribution Workflow

```bash
# 1. Create feature branch
git checkout -b feature/your-feature

# 2. Make changes
# ...

# 3. Run tests + lint
make test-unit
make lint-fix

# 4. Commit and push
git commit -m "feat: add feature description"
git push origin feature/your-feature

# 5. Create Pull Request
```

---

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.

---

## Acknowledgments

Built with:
- [PostgreSQL](https://www.postgresql.org/) - Database
- [HuggingFace](https://huggingface.co/) - Vault storage
- [Prefect](https://www.prefect.io/) - Workflow orchestration
- [aiohttp](https://docs.aiohttp.org/) - Async HTTP

---

**Version**: 1.0.0
**Maintainer**: Ahmad Saeed Zaidi
**Last Updated**: 2026-07-14
