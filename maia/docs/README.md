# Maia Documentation

**Collection service agents for the Pleiades platform**

> For complete platform documentation, see **[`../../docs/`](../../docs/README.md)**

---

## Quick Links

- **[Main Documentation](../../docs/README.md)** - Platform overview and architecture
- **[Quick Start Guide](../../docs/quickstart.md)** - Get up and running
- **[Adaptive Scheduling](../../docs/ghost-tracking.md)** - Persistent video tracking
- **[Resiliency Strategy](../../docs/hydra-protocol.md)** - API key management
- **[Tiered Storage Architecture](../../docs/hot-queue.md)** - Ephemeral data pattern
- **[Testing Guide](../../docs/testing.md)** - Test suite and coverage
- **[Contributing](../../docs/contributing.md)** - Development workflow

---

## Maia Agents Overview

Maia is a multi-agent system for YouTube video collection. Each agent has a focused responsibility:

### Hunter (`hunter/`)
**Purpose**: Discover new viral videos

**Responsibilities**:
- Search YouTube with multiple queries
- Filter videos published in last 24 hours
- Add videos to Adaptive Scheduling watchlist
- Extract tags for snowball effect

**CLI**:
```bash
maia-hunter
```

---

### Tracker (`tracker/`)
**Purpose**: Monitor viral velocity and metrics

**Responsibilities**:
- Fetch from persistent watchlist (Adaptive Scheduling)
- Query YouTube Statistics API
- Log metrics to hot tier (SQL)
- Update adaptive tracking schedule

**CLI**:
```bash
maia-tracker
```

---

### Scribe (`scribe/`)
**Purpose**: Extract video transcripts

**Responsibilities**:
- Download transcripts via YouTube API
- Store to Vault (cold tier)
- Mark videos as transcript-safe
- Retry with exponential backoff

**CLI**:
```bash
maia-scribe
```

---

### Painter (`painter/`)
**Purpose**: Collect visual evidence (keyframes)

**Responsibilities**:
- Download videos via yt-dlp
- Extract keyframes using OpenCV
- Store frames to Vault (Parquet)
- Mark videos as visuals-safe

**CLI**:
```bash
maia-painter
```

---

### Janitor (`janitor/`)
**Purpose**: Hot queue cleanup and stats archival

**Responsibilities**:
- Archive stats from SQL to Vault (7-day retention)
- Delete old processed videos
- Preserve Adaptive Scheduling watchlist
- Dry-run mode for safety

**CLI**:
```bash
maia-janitor
```

---

## Agent Details

For detailed agent information, see **[agents.md](agents.md)**

---

## Development

### Installation

```bash
cd maia
pip install -e ".[dev]"
```

### Testing

```bash
# Unit tests
pytest tests/

# Integration tests (requires Atlas)
cd ../alkyone && pytest tests/components/maia/

# With coverage
pytest --cov=maia --cov-report=html
```

### Configuration

All agents share Atlas configuration. See **[../atlas/ENV.example](../atlas/ENV.example)** for required environment variables.

**Key settings**:
```bash
DATABASE_URL=postgresql://user:pass@host:5432/db
VAULT_PROVIDER=huggingface  # or 'gcs'
YOUTUBE_API_KEY_POOL_JSON='["key1","key2"]'
JANITOR_ENABLED=true
JANITOR_RETENTION_DAYS=7
```

---

## Architecture Patterns

### Adaptive Scheduling
Videos tracked forever via `watchlist` table, even after Janitor cleanup.

**See**: [docs/ghost-tracking.md](../../docs/ghost-tracking.md)

### Resiliency Strategy
Multi-key rotation with clean termination (exit 0) on quota exhaustion.

**See**: [docs/hydra-protocol.md](../../docs/hydra-protocol.md)

### Hot/Cold Storage
7-day SQL retention, unlimited Vault storage via Parquet.

**See**: [docs/hot-queue.md](../../docs/hot-queue.md)

---

## Related Documentation

- **[Atlas](../atlas/docs/README.md)** - Infrastructure layer
- **[Alkyone](../alkyone/README.md)** - Integration testing

---

**Maintainer**: Ahmad Saeed Zaidi  
**Last Updated**: 2026-01-15
