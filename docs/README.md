# Pleiades Documentation

**Project Pleiades** is a viral video intelligence platform that discovers, tracks, and analyzes YouTube content at scale.

---

## Documentation Index

### Getting Started
- **[Quick Start](quickstart.md)** - Get up and running in 5 minutes
- **[Architecture Overview](architecture.md)** - System design and components

### Core Features
- **[Adaptive Scheduling](adaptive-scheduling.md)** - Infinite video tracking with minimal SQL footprint
- **[Resiliency Strategy](resiliency-strategy.md)** - Intelligent API key management and rotation
- **[Tiered Storage Architecture](tiered-storage.md)** - Ephemeral data management for high-throughput ingestion

### Component Guides
- **[Atlas](../atlas/docs/README.md)** - Infrastructure layer (DB, Vault, Events, Notifications)
- **[Maia](../maia/docs/README.md)** - Collection service (Hunter, Tracker agents)
- **[Alkyone](../alkyone/README.md)** - Integration testing suite

### Development
- **[Testing Guide](testing.md)** - Unit, integration, and smoke testing
- **[Contributing](contributing.md)** - Development workflow and standards

---

## Quick Links

### For New Users
1. Read [Architecture Overview](architecture.md)
2. Follow [Quick Start](quickstart.md)
3. Review component-specific guides

### For Developers
1. Read [Contributing](contributing.md)
2. Set up local environment
3. Run tests with `make test`

### For Operators
1. Review deployment architecture
2. Configure environment variables
3. Monitor with Resiliency Strategy guidelines

---

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      PLEIADES PLATFORM                        │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    MAIA SERVICE                      │   │
│  │                                                      │   │
│  │  Hunter Agent  → Discover new videos               │   │
│  │  Tracker Agent → Monitor viral velocity            │   │
│  │  (Adaptive Scheduling for infinite history)             │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                  ATLAS LIBRARY                       │   │
│  │                                                      │   │
│  │  Database   → PostgreSQL (Tiered Storage <7 days)       │   │
│  │  Vault      → HF/GCS (Cold storage, Parquet)       │   │
│  │  Events     → Observer pattern event bus            │   │
│  │  Notifier   → Alerts and notifications             │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                ALKYONE TEST SUITE                    │   │
│  │  Integration & smoke tests for all components       │   │
│  └─────────────────────────────────────────────────────┘   │
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

[Learn more →](ghost-tracking.md)

### 🔑 Resiliency Strategy
Intelligent API key management:
- Automatic key rotation on quota exhaustion
- Clean termination (SystemExit) when all keys exhausted
- KeyRing for pool management
- HydraExecutor for automatic retry logic

[Learn more →](hydra-protocol.md)

### ⚡ Tiered Storage Architecture
Ephemeral data management for high throughput:
- Videos purged after 7 days
- Search queue for discovery coordination
- Watchlist persists forever (Adaptive Scheduling)
- Maintains <0.5 GB SQL footprint

[Learn more →](hot-queue.md)

---

## Project Structure

```
pleiades/
├── docs/                    # Unified project documentation
│   ├── README.md            # This file
│   ├── quickstart.md        # Getting started guide
│   ├── architecture.md      # System architecture
│   ├── adaptive-scheduling.md  # Adaptive Scheduling guide
│   ├── resiliency-strategy.md  # Resiliency Strategy guide
│   ├── tiered-storage.md    # Tiered Storage architecture
│   ├── testing.md           # Testing guide
│   └── contributing.md      # Development guide
│
├── atlas/                   # Infrastructure library
│   ├── src/atlas/
│   ├── docs/                # Atlas-specific docs
│   └── tests/               # Atlas unit tests
│
├── maia/                    # Collection service
│   ├── src/maia/
│   ├── docs/                # Maia-specific docs
│   └── tests/               # Maia unit tests
│
└── alkyone/                 # Integration testing
    ├── src/alkyone/
    └── tests/               # Integration & smoke tests
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
