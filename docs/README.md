# Pleiades Documentation

**Project Pleiades** is a viral video intelligence platform that discovers, tracks, and analyzes YouTube content at scale.

---

## Documentation Index

### Getting Started
- **[Quick Start](quickstart.md)** - Get up and running
- **[Architecture Overview](architecture.md)** - System design and components
- **[Deployment (two-VPS)](deploy.md)** - Control plane + executor setup, no secrets
- **[Orchestration Runbook](micro-prefect-orchestration.md)** - Everyday ops & incident playbooks

### Core Features
- **[Adaptive Scheduling](adaptive-scheduling.md)** - Infinite video tracking with minimal SQL footprint
- **[Resiliency Strategy](resiliency-strategy.md)** - Intelligent API key management and rotation
- **[Tiered Storage Architecture](tiered-storage.md)** - Ephemeral data management for high-throughput ingestion

### Component Guides
- **[Atlas](../atlas/README.md)** - Infrastructure layer (DB, Vault, Events, Notifications, Repositories)
- **[Maia](../maia/README.md)** - Stateless agent fleet (Hunter, Tracker, Scribe, Painter, …)
- **[Alkyone](../alkyone/README.md)** - Integration testing suite (isolated infra only)

### Development & Ops
- **[Testing Guide](testing.md)** - Unit, integration, and smoke testing
- **[Contributing](contributing.md)** - Development workflow and standards
- **[Challenges Log](challenges.md)** - Problems we hit and how we solved them

---

## Quick Links

### For New Users
1. Read [Architecture Overview](architecture.md)
2. Follow [Quick Start](quickstart.md)
3. Review component-specific guides
4. Stand up the system with [Deployment](deploy.md)

### For Developers
1. Read [Contributing](contributing.md)
2. Set up local environment
3. Run tests with `make test`

### For Operators
1. Read [Deployment](deploy.md) (two-VPS setup)
2. Read the [Orchestration Runbook](micro-prefect-orchestration.md)
3. Keep [Challenges](challenges.md) handy for known traps

---

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE (micro)                     │
│  Prefect server/API :4200  (SQLite orchestration DB)          │
└───────────────────────────────┬──────────────────────────────┘
                                 │ PREFECT_API_URL
                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                      EXECUTOR VPS (2-core)                     │
│                                                                │
│  ┌─────────────────────────────────────────────────────┐     │
│  │                  MAIA AGENT FLEET                    │     │
│  │  Hunter/Archeologist (producers) → videos table →   │     │
│  │  Scribe/Painter/Singer/Tracker/Janitor/Heartbeat     │     │
│  │  (consumers) + Streamer (audio producer)            │     │
│  └───────────────────────────────┬─────────────────────┘     │
│                                  ▼                            │
│  ┌─────────────────────────────────────────────────────┐     │
│  │                  ATLAS LIBRARY                       │     │
│  │  Database   → PostgreSQL (Tiered Storage <7 days)    │     │
│  │  Vault      → HF/GCS (Cold storage, Parquet/WebP)    │     │
│  │  Repositories → VideoRepository, ChannelRepository…  │     │
│  │  Events     → Observer-pattern event bus             │     │
│  │  Notifier   → Discord alerts                         │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                                │
│  ┌─────────────────────────────────────────────────────┐     │
│  │           ALKYONE TEST SUITE (isolated infra)        │     │
│  │  Integration & smoke tests — never production        │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

---

## Key Features

### 🚀 Adaptive Scheduling
Track videos **forever** while keeping SQL under 0.5 GB:
- Lightweight `watchlist` table in SQL for scheduling
- Heavy time-series metrics in Parquet files (Vault)
- Adaptive tracking tiers (HOURLY → DAILY → WEEKLY)
- Decoupled from video retention (survives Janitor cleanup)

[Learn more →](adaptive-scheduling.md)

### 🔑 Resiliency Strategy
Intelligent API key management:
- Automatic key rotation on quota exhaustion (`KeyRing` + `ResiliencyExecutor`)
- `QuotaExhaustedError` propagates and fires a Discord alert (no `sys.exit`)
- Rate-limit errors release the video to `PENDING` for retry

[Learn more →](resiliency-strategy.md)

### ⚡ Tiered Storage Architecture
Ephemeral data management for high throughput:
- Videos archived to the vault after the retention window (default 7 days)
- Search queue for discovery coordination
- Watchlist persists forever (Adaptive Scheduling)
- Maintains <0.5 GB SQL footprint

[Learn more →](tiered-storage.md)

---

## Project Structure

```
pleiades/
├── docs/                    # Unified project documentation
│   ├── README.md            # This file
│   ├── deploy.md            # Two-VPS deployment guide
│   ├── micro-prefect-orchestration.md  # Ops runbook
│   ├── challenges.md        # Engineering challenges log
│   ├── quickstart.md        # Getting started guide
│   ├── architecture.md      # System architecture
│   ├── adaptive-scheduling.md
│   ├── resiliency-strategy.md
│   ├── tiered-storage.md
│   ├── testing.md           # Testing guide
│   └── contributing.md      # Development guide
│
├── atlas/                   # Infrastructure library
│   ├── src/atlas/
│   └── README.md            # Atlas-specific docs
│
├── maia/                    # Stateless agent fleet
│   ├── src/maia/
│   └── README.md            # Maia-specific docs
│
└── alkyone/                 # Integration testing (isolated infra)
    └── README.md
```

---

## Support

- **Documentation**: Start here and follow component-specific guides
- **Issues**: Report bugs or request features on GitHub
- **Contributing**: Read [Contributing Guide](contributing.md)

---

**Version**: 1.0.0
**License**: MIT
**Maintainer**: Ahmad Saeed Zaidi
**Last Updated**: 2026-07-14
