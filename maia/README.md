# Maia - The Agent Layer

**Maia** is the stateless agent layer of Project Pleiades, built on Prefect for high-velocity video discovery and viral velocity monitoring.

## Architecture

Maia operates as a **stateless agent** with two core responsibilities:

### 🔍 The Hunter (Discovery & Ingestion)
- Fetches queries from the search queue using `FOR UPDATE SKIP LOCKED` for race-free concurrency
- Searches YouTube API with automatic key rotation
- Ingests video metadata into Atlas (hot index + cold archive)
- Implements "Snowball Effect": extracts tags from videos and feeds them back into the search queue
- **Hydra Protocol**: On 429 rate limit, immediately terminates (suicide) to force container restart and IP rotation

### 📊 The Tracker (Velocity Monitoring)
- Implements "3-Zone Defense" strategy for freshness:
  - **Zone 1** (Hot): Videos <24h old, updated hourly
  - **Zone 2** (Warm): Videos 1-7 days old, updated every 6 hours
  - **Zone 3** (Cold): Videos >7 days old, updated daily
- Batches up to 50 video IDs per API call for efficiency
- Updates view counts, likes, and comments in TimescaleDB hypertables

## Core Directives

1. **Statelessness**: Maia holds no local state. All persistence is handled by Atlas.
2. **DAO Pattern**: Maia NEVER writes raw SQL. All database interactions occur via `atlas.adapters.maia.MaiaDAO`.
3. **Hydra Protocol**: On rate limit (429), immediately raise `SystemExit` to trigger container restart and IP rotation.
4. **Key Isolation**: Separate key rings for hunting and tracking to prevent quota contamination.

## Installation

```bash
# From the maia directory
pip install -e .

# With dev dependencies
pip install -e ".[dev]"
```

## Configuration

Maia inherits configuration from Atlas via environment variables. Create a `.env` file (use `.env.example` as template):

```bash
# Required: Atlas configuration
DATABASE_URL="postgresql://..."
YOUTUBE_API_KEY_POOL_JSON='["key1", "key2", "key3"]'
VAULT_PROVIDER="huggingface"  # or "gcs"

# Key Pool Allocation
KEY_POOL_TRACKING_SIZE=2       # Number of keys reserved for Tracker
KEY_POOL_ARCHEOLOGY_SIZE=1     # Number of keys reserved for future Scribe

# Optional: Prefect Cloud
PREFECT_API_URL="https://api.prefect.cloud/..."
PREFECT_API_KEY="pnu_..."
```

## Usage

### Run Hunter (Discovery)
```bash
# Direct execution
python -m maia.hunter

# Or via entry point
maia-hunter
```

### Run Tracker (Monitoring)
```bash
# Direct execution
python -m maia.tracker

# Or via entry point
maia-tracker
```

### Docker Deployment
```bash
# Build the Hydra image (from project root)
docker build -f maia/Dockerfile -t pleiades-maia:latest .

# Run Hunter
docker run --env-file .env pleiades-maia:latest python -m maia.hunter

# Run Tracker
docker run --env-file .env pleiades-maia:latest python -m maia.tracker
```

## Development

### Run Tests
```bash
pytest
```

### Format Code
```bash
black src/
isort src/
```

### Type Check
```bash
mypy src/
```

## Architecture Integration

```
┌─────────────────────────────────────────────────┐
│                    Maia                         │
│  ┌──────────────┐         ┌──────────────┐     │
│  │   Hunter     │         │   Tracker    │     │
│  │ (Discovery)  │         │ (Monitoring) │     │
│  └──────┬───────┘         └──────┬───────┘     │
│         │                        │             │
│         └────────┬───────────────┘             │
│                  │                             │
│         ┌────────▼────────┐                    │
│         │    MaiaDAO      │                    │
│         │  (Atlas API)    │                    │
│         └────────┬────────┘                    │
└──────────────────┼──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│                  Atlas                          │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐       │
│  │   DB    │  │  Vault  │  │  Events  │       │
│  │(Postgres)│  │ (Cold)  │  │(Timescale)│      │
│  └─────────┘  └─────────┘  └──────────┘       │
└─────────────────────────────────────────────────┘
```

## Error Handling

### Hunter
- **403 (Quota Exceeded)**: Rotates to next key in the ring
- **429 (Rate Limit)**: Raises `SystemExit` (Hydra Protocol - container suicide)
- **Network Errors**: Logged and skipped
- **Exhausted Key Ring**: Logs critical error and skips query

### Tracker
- **403/429**: Same behavior as Hunter
- **Invalid Response**: Logged and batch skipped
- **Partial Failures**: Successfully processed videos are still updated

## Contributing

Maia follows the same development standards as Atlas:
1. All imports from Atlas must use `from atlas.adapters.maia import MaiaDAO`
2. Never write raw SQL - use MaiaDAO methods exclusively
3. Always handle rate limits with immediate termination
4. Maintain statelessness - no local caching or persistence
5. Follow type hints strictly (mypy strict mode)

## License

MIT License - See LICENSE file for details.

