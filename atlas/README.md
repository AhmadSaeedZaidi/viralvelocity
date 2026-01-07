# Atlas

> Core infrastructure library for the Pleiades Surveillance Platform

Atlas provides unified interfaces for persistence, storage, observability, and governance across the Pleiades ecosystem.

## Features

- **Database Layer**: Serverless Postgres connection pooling with health checks
- **Storage Strategy**: Dual-vault support (HuggingFace/GCS) with swappable backends
- **Event Bus**: Immutable event sourcing with async emission
- **Notifications**: Discord webhook routing with channel-based alerting
- **Type Safe**: 100% type-annotated with strict mypy compliance

## Quick Start

```bash
# Install
cd atlas
poetry install --extras all --with dev

# Configure
cp ENV.example .env
# Edit .env with your credentials

# Provision database
make setup

# Verify connectivity
make smoke-test

# Run tests
make test
```

## Usage

```python
from atlas import db, vault, events, notifier, AlertLevel

# Database
async with db.get_connection() as conn:
    result = await conn.execute("SELECT * FROM channels")

# Storage
vault.store_json("metadata/video.json", {"id": "abc123"})
data = vault.fetch_json("metadata/video.json")

# Events
await events.emit("video.discovered", "abc123", {"title": "Example"})

# Notifications
await notifier.send(
    "Discovery Complete",
    "Found 10 new videos",
    level=AlertLevel.SUCCESS
)
```

## Documentation

- **[Quick Start](docs/quickstart.md)** - Get up and running in 5 minutes
- **[API Reference](docs/api-reference.md)** - Complete API documentation
- **[Architecture](docs/architecture.md)** - Design patterns and system architecture
- **[Contributing](docs/contributing.md)** - Development workflow and guidelines
- **[Migration Guide](docs/migration.md)** - Upgrading from v0.1.x
- **[Changelog](CHANGELOG.md)** - Version history

## Environment Variables

See [ENV.example](ENV.example) for configuration template.

**Required:**
- `DATABASE_URL` - PostgreSQL connection string
- `YOUTUBE_API_KEY_POOL_JSON` - JSON array of API keys
- `VAULT_PROVIDER` - Storage backend (`huggingface` or `gcs`)

**Provider-specific:**
- HuggingFace: `HF_DATASET_ID`, `HF_TOKEN`
- GCS: `GCS_BUCKET_NAME`, `GOOGLE_APPLICATION_CREDENTIALS`

## Development

```bash
make help          # Show all commands
make install       # Install dependencies
make setup         # Provision database
make test          # Run unit tests
make smoke-test    # Verify live services
make format        # Format code
make lint          # Check style
make type-check    # Run mypy
```

## License

MIT License - See [LICENSE.md](../LICENSE.md)

## Author

Ahmad Saeed Zaidi
