# Pleiades

**Viral video intelligence platform for YouTube content discovery and tracking at scale**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## Overview

Pleiades is a high-throughput video intelligence platform that:

- 🔍 **Discovers** viral content through intelligent YouTube search
- 📊 **Tracks** video metrics forever with minimal SQL footprint (<0.5 GB)
- 🚀 **Scales** to 100k+ videos/day using Adaptive Scheduling architecture
- 🔑 **Manages** API quotas intelligently via Resiliency Strategy
- 🗄️ **Stores** time-series data efficiently in Parquet files

---

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Neon serverless)
- HuggingFace account or Google Cloud Storage
- YouTube Data API v3 keys

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/pleiades.git
cd pleiades

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install components
cd atlas && pip install -e ".[dev]" && cd ..
cd maia && pip install -e ".[dev]" && cd ..
cd alkyone && pip install -e . && cd ..
```

### Configuration

```bash
# Copy environment templates
cp atlas/ENV.example atlas/.env
cp maia/ENV.example maia/.env

# Edit .env files with your credentials
```

### Initialize Database

```bash
cd atlas
make setup
make smoke-test
```

### Run Services

```bash
# Hunter (discovery)
cd maia && python -m maia.hunter.flow

# Tracker (monitoring)
python -m maia.tracker.flow

# Or use Docker Compose
docker-compose up -d
```

**See [Quick Start Guide](docs/quickstart.md) for detailed instructions.**

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    PLEIADES PLATFORM                      │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │               MAIA SERVICE                      │    │
│  │  • Hunter Agent  → Discover new videos         │    │
│  │  • Tracker Agent → Monitor viral velocity      │    │
│  │  • Janitor Agent → Clean up old data           │    │
│  └────────────────────────────────────────────────┘    │
│                        ↓                                 │
│  ┌────────────────────────────────────────────────┐    │
│  │             ATLAS LIBRARY                       │    │
│  │  • Database   → PostgreSQL (Tiered Storage)    │    │
│  │  • Vault      → HF/GCS (Cold storage)          │    │
│  │  • Events     → Event bus                      │    │
│  │  • Notifier   → Alerts                         │    │
│  └────────────────────────────────────────────────┘    │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │           ALKYONE TEST SUITE                    │    │
│  │  Integration & smoke tests                     │    │
│  └────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**See [Architecture Guide](docs/architecture.md) for complete design.**

---

## Key Features

### 🎯 Adaptive Scheduling

Track videos **forever** while keeping SQL under 0.5 GB:

- Lightweight `watchlist` table for scheduling (50 bytes/video)
- Heavy metrics stored in Parquet files (unlimited)
- Adaptive tracking tiers (HOURLY → DAILY → WEEKLY)
- Survives video cleanup (tracking continues after deletion)

**Example**:
```python
# Add video to persistent watchlist
await dao.add_to_watchlist("VIDEO_123", tier="HOURLY")

# Track continues forever, even after Janitor cleanup
```

**[Learn more →](docs/adaptive-scheduling.md)**

### 🔑 Resiliency Strategy

Intelligent API key management:

- Automatic key rotation on quota exhaustion
- Clean termination (exit 42) when all keys exhausted
- Container orchestration integration
- Retry logic with exponential backoff

**Example**:
```python
keys = KeyRing("hunting")
executor = HydraExecutor(keys, agent_name="hunter")
result = await executor.execute_async(make_request)
# Automatically rotates through keys on 403/429
```

**[Learn more →](docs/resiliency-strategy.md)**

### ⚡ Tiered Storage Architecture

Ephemeral data management for high throughput:

- Videos auto-deleted after 7 days
- SQL footprint stays constant (<0.5 GB)
- Fast queries on recent data only
- 100k+ videos/day ingestion capacity

**[Learn more →](docs/tiered-storage.md)**

---

## Documentation

### Getting Started
- **[Quick Start](docs/quickstart.md)** - Get up and running in 5 minutes
- **[Architecture Overview](docs/architecture.md)** - System design and components

### Core Features
- **[Adaptive Scheduling](docs/adaptive-scheduling.md)** - Infinite video tracking
- **[Resiliency Strategy](docs/resiliency-strategy.md)** - API key management
- **[Tiered Storage](docs/tiered-storage.md)** - Ephemeral data management

### Development
- **[Testing Guide](docs/testing.md)** - Unit, integration, and smoke testing
- **[Contributing](docs/contributing.md)** - Development workflow and standards

### Component Guides
- **[Atlas](atlas/docs/README.md)** - Infrastructure layer
- **[Maia](maia/docs/README.md)** - Collection service
- **[Alkyone](alkyone/README.md)** - Integration testing

---

## Project Structure

```
pleiades/
├── docs/                    # 📚 Unified documentation
│   ├── README.md            # Documentation index
│   ├── quickstart.md        # Getting started
│   ├── architecture.md      # System design
│   ├── adaptive-scheduling.md  # Adaptive Scheduling guide
│   ├── resiliency-strategy.md  # Resiliency Strategy guide
│   ├── tiered-storage.md    # Tiered Storage architecture
│   ├── testing.md           # Testing guide
│   └── contributing.md      # Development guide
│
├── atlas/                   # 🏗️ Infrastructure library
│   ├── src/atlas/
│   │   ├── db.py            # PostgreSQL
│   │   ├── vault.py         # HF/GCS storage
│   │   ├── events.py        # Event bus
│   │   ├── notifier.py      # Alerts
│   │   ├── utils.py         # KeyRing, HydraExecutor
│   │   ├── schema.sql       # Database schema
│   │   └── adapters/
│   │       ├── maia.py      # MaiaDAO
│   │       └── maia_adaptive_scheduling.py # Ghost Tracking
│   ├── docs/                # Atlas-specific docs
│   └── tests/               # Unit tests
│
├── maia/                    # 🤖 Collection service
│   ├── src/maia/
│   │   ├── hunter/          # Discovery agent
│   │   ├── tracker/         # Monitoring agent
│   │   ├── janitor/         # Cleanup agent
│   │   ├── painter/         # Enrichment agent
│   │   └── scribe/          # Feature extraction
│   ├── docs/                # Maia-specific docs
│   └── tests/               # Unit tests
│
└── alkyone/                 # 🧪 Integration testing
    ├── src/alkyone/
    │   └── fixtures.py      # Test fixtures
    └── tests/components/    # Integration tests
```

---

## Usage Examples

### Discovery (Hunter)

```python
from maia.hunter import run_hunter_cycle

# Run discovery cycle
stats = await run_hunter_cycle(batch_size=10)
# Discovers videos, adds to watchlist (Ghost Tracking)
```

### Monitoring (Tracker)

```python
from maia.tracker import run_tracker_cycle

# Run tracking cycle
stats = await run_tracker_cycle(batch_size=50)
# Fetches from watchlist, stores to Vault
```

### Data Access

```python
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()

# Add video to persistent watchlist
await dao.add_to_watchlist("VIDEO_123")

# Fetch videos needing updates
batch = await dao.fetch_tracking_batch(50)

# Store metrics to Vault
from atlas import vault
vault.append_metrics(metrics_data)
```

---

## Performance

- **Throughput**: 100k+ videos/day
- **SQL Footprint**: <0.5 GB (constant)
- **Tracking Duration**: Infinite (Ghost Tracking)
- **API Efficiency**: Multi-key rotation (Hydra Protocol)
- **Storage**: Unlimited (compressed Parquet in Vault)

---

## Testing

```bash
# Unit tests
cd atlas && pytest tests/
cd maia && pytest tests/

# Integration tests
cd alkyone && pytest tests/

# Smoke tests (requires live services)
pytest -m smoke

# With coverage
pytest --cov=atlas --cov=maia --cov-report=html
```

**See [Testing Guide](docs/testing.md) for detailed instructions.**

---

## Deployment

### Docker Compose

```yaml
services:
  maia-hunter:
    build: ./maia
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - YOUTUBE_API_KEY_POOL_JSON=${YOUTUBE_API_KEY_POOL_JSON}
    restart: on-failure:5
    command: python -m maia.hunter.flow

  maia-tracker:
    build: ./maia
    environment:
      - DATABASE_URL=${DATABASE_URL}
    restart: on-failure:5
    command: python -m maia.tracker.flow
```

```bash
docker-compose up -d
```

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

# 3. Run tests
pytest --cov=atlas --cov=maia

# 4. Format code
cd atlas && make format
cd maia && make format

# 5. Commit and push
git commit -m "feat: add feature description"
git push origin feature/your-feature

# 6. Create Pull Request
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

## Support

- **Documentation**: [docs/README.md](docs/README.md)
- **Issues**: [GitHub Issues](https://github.com/yourusername/pleiades/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/pleiades/discussions)

---

**Version**: 1.0.0  
**Maintainer**: Ahmad Saeed Zaidi  
**Last Updated**: 2026-01-15
