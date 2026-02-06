# Atlas Documentation

**Infrastructure layer for the Pleiades platform**

> For complete platform documentation, see **[`../../docs/`](../../docs/README.md)**

---

## Quick Links

- **[Main Documentation](../../docs/README.md)** - Platform overview and architecture  
- **[Quick Start Guide](../../docs/quickstart.md)** - Get up and running
- **[Architecture](../../docs/architecture.md)** - System design and patterns
- **[Testing Guide](../../docs/testing.md)** - Test suite and coverage
- **[Contributing](../../docs/contributing.md)** - Development workflow

---

## Atlas Components

Atlas provides core infrastructure for video data management.

### Database (`db.py`)
- PostgreSQL connection management
- Async connection pooling
- Transaction support

### Vault (`vault.py`)
- HuggingFace Hub integration (`HuggingFaceVault`)
- Google Cloud Storage integration (`GCSVault`)
- Parquet file storage for time-series data
- Metrics appending for hot/cold storage

### Data Access (`adapters/`)
- `MaiaDAO`: Data access for Maia service
- `GhostTrackingMixin`: Persistent video tracking
- Hot/cold tier management
- Stats archival logic

### Utilities (`utils.py`)
- `KeyRing`: API key rotation with session tracking
- `HydraExecutor`: Unified API request executor
- Retry decorators with exponential backoff

### Events (`events.py`)
- Event bus for system-wide notifications
- Webhook delivery
- Event filtering and routing

### Notifications (`notifications.py`)
- Discord webhook integration
- Formatted message delivery
- Error tracking

---

## API Reference

For detailed API documentation, see **[api-reference.md](api-reference.md)**

---

## Database Schema

Located in `src/atlas/schema.sql`:

### Core Tables
- `videos` - Video metadata (7-day retention)
- `video_stats_log` - Stats hot tier (7-day retention)
- `watchlist` - Adaptive Scheduling schedule (persistent)
- `channels` - Channel metadata
- `search_queue` - Hunter query queue

### Key Indexes
- `idx_video_status` - Status-based queries
- `idx_watchlist_next_track` - Efficient batch fetching
- `idx_stats_timestamp` - Time-range queries

---

## Configuration

Set via environment variables:

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/db

# Vault (choose one)
VAULT_PROVIDER=huggingface  # or 'gcs'
HF_TOKEN=hf_xxxxxxxxxxxxx
HF_REPO_ID=username/repo-name

# Or for GCS
GCS_BUCKET_NAME=your-bucket
GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# API Keys
YOUTUBE_API_KEY_POOL_JSON='["key1","key2","key3"]'

# Janitor
JANITOR_ENABLED=true
JANITOR_RETENTION_DAYS=7
JANITOR_SAFETY_CHECK=true

# Events (optional)
EVENT_BUS_ENABLED=true
EVENT_BUS_WEBHOOK_URL=https://your-webhook.com
```

See **[ENV.example](../ENV.example)** for complete configuration.

---

## Development

### Setup

```bash
cd atlas
pip install -e ".[dev]"
```

### Testing

```bash
# Unit tests
pytest tests/

# Smoke tests (requires live services)
pytest -m smoke

# With coverage
pytest --cov=atlas --cov-report=html
```

### Linting

```bash
# Check code quality
python -m black --check src/
python -m isort --check src/

# Auto-format
python -m black src/
python -m isort src/
```

---

## Usage Examples

### Database Access

```python
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()

# Video operations
await dao.ingest_video_metadata(video_data)
await dao.fetch_scribe_batch(10)
await dao.mark_video_done("VIDEO_123")

# Adaptive Scheduling
await dao.add_to_watchlist("VIDEO_123", tier="HOURLY")
batch = await dao.fetch_tracking_batch(50)

# Hot/Cold Storage
await dao.log_video_stats_batch(stats_list)
archived = await dao.archive_cold_stats(retention_days=7)
```

### Vault Storage

```python
from atlas.vault import vault

# Store transcript
vault.store_transcript("VIDEO_123", transcript_data)

# Append metrics (hot/cold storage)
metrics = [{"video_id": "V123", "views": 1000, ...}]
vault.append_metrics(metrics, date="2026-01-15")
```

### Key Management

```python
from atlas.utils import KeyRing, HydraExecutor

# Initialize key ring
keys = KeyRing("hunting")
executor = HydraExecutor(keys, agent_name="hunter")

# Execute with automatic rotation
async def make_request(api_key: str):
    # Your API call here
    return await fetch_data(api_key)

result = await executor.execute_async(make_request)
```

---

## Architecture Patterns

### Tiered Storage
- 7-day retention in SQL
- Constant database size (<0.5 GB)
- Fast queries on recent data

**See**: [docs/hot-queue.md](../../docs/hot-queue.md)

### Adaptive Scheduling
- Persistent `watchlist` table
- Survives Janitor cleanup
- Adaptive tracking tiers

**See**: [docs/ghost-tracking.md](../../docs/ghost-tracking.md)

### Resiliency Strategy
- Multi-key rotation
- Clean termination (exit 0)
- Quota management

**See**: [docs/hydra-protocol.md](../../docs/hydra-protocol.md)

---

## Related Documentation

- **[Maia](../maia/docs/README.md)** - Collection service agents
- **[Alkyone](../alkyone/README.md)** - Integration testing

---

**Maintainer**: Ahmad Saeed Zaidi  
**Last Updated**: 2026-01-15