# Quick Start Guide

Get Atlas running in 5 minutes.

## Prerequisites

- Python 3.11+
- Poetry
- PostgreSQL with pgvector extension
- Storage provider account (HuggingFace or GCS)

## Installation

```bash
cd atlas
poetry install --extras all --with dev
```

## Configuration

Copy the environment template:

```bash
cp ENV.example .env
```

Edit `.env` with your credentials:

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/pleiades

# Storage (choose one)
VAULT_PROVIDER=huggingface
HF_DATASET_ID=username/dataset-name
HF_TOKEN=hf_xxxxxxxxxxxxx

# API Keys
YOUTUBE_API_KEY_POOL_JSON=["AIza..."]

# Optional
COMPLIANCE_MODE=true
ENV=dev
```

## Database Setup

Provision the schema:

```bash
make setup
```

Expected output:
```
Provisioning database schema...
Schema provisioned
```

## Verification

Run smoke tests to verify connectivity:

```bash
make smoke-test
```

This checks:
- Database connection to Neon
- Vault provider configuration
- API keys loaded correctly
- Environment variables present

## Basic Usage

Create a test script:

```python
import asyncio
from atlas import db, vault, events, notifier, AlertLevel, AlertChannel

async def main():
    # Database query
    async with db.get_connection() as conn:
        result = await conn.execute("SELECT COUNT(*) FROM channels")
        print(f"Channels: {result}")
    
    # Storage operations (structured layout)
    vault.store_metadata("test_video", {"title": "Example", "tags": ["ai", "ml"]})
    vault.store_transcript("test_video", {"text": "Hello Atlas"})
    
    # Fetch data
    metadata = vault.fetch_metadata("test_video", "2026-01-09")
    transcript = vault.fetch_transcript("test_video")
    print(f"Retrieved: {metadata}, {transcript}")
    
    # Event emission
    await events.emit("test.event", "entity123", {"status": "ok"})
    print("Event emitted")
    
    # Notification
    await notifier.send(
        "System Initialized",
        "Atlas is ready",
        channel=AlertChannel.OPS,
        level=AlertLevel.SUCCESS
    )
    print("Notification sent")
    
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
```

Run it:
```bash
poetry run python test_atlas.py
```

## Troubleshooting

### Database Connection Failed
- Verify `DATABASE_URL` format: `postgresql://user:pass@host:port/dbname`
- Ensure PostgreSQL is running and accessible
- Check pgvector extension: `CREATE EXTENSION IF NOT EXISTS vector;`

### Vault Import Error
Install provider dependencies:
```bash
poetry install --extras hf   # HuggingFace
poetry install --extras gcs  # Google Cloud Storage
```

### Configuration Validation Error
Ensure provider-specific variables are set:
- **HuggingFace**: `HF_DATASET_ID` and `HF_TOKEN`
- **GCS**: `GCS_BUCKET_NAME` and credentials via environment

### Smoke Tests Failing
Check your `.env` file has all required variables and that services are accessible from your network.

## Next Steps

- **Detailed Usage**: See [Architecture Guide](architecture.md) for component details
- **Development**: Read [Contributing Guide](contributing.md) for development workflow
- **Examples**: 
  - `examples/basic_usage.py` - Core functionality patterns
  - `examples/database_adapter.py` - MaiaDAO adapter usage
- **Custom Adapters**: See [Contributing Guide](contributing.md#creating-a-custom-database-adapter) for building your own DAO
- **Integration**: Import Atlas in downstream services (Maia, etc.)

## Development Workflow

```bash
# 1. Make changes to source
vim src/atlas/...

# 2. Format code
make format

# 3. Run checks
make lint
make type-check

# 4. Test
make test
make smoke-test

# 5. Commit
git commit -m "feat: your change"
```

## Common Commands

```bash
make help         # Show all available commands
make install      # Install dependencies
make setup        # Provision database
make test         # Run unit tests
make smoke-test   # Verify live connectivity
make format       # Auto-format code
make lint         # Check code style
make type-check   # Run mypy
make clean        # Clean artifacts
```

