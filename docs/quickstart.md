# Quick Start Guide

Get Pleiades running in 5 minutes.

---

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Neon serverless)
- HuggingFace account (for Vault storage) OR Google Cloud Storage
- YouTube Data API v3 keys

---

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/pleiades.git
cd pleiades
```

### 2. Set Up Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Components

```bash
# Install Atlas (infrastructure)
cd atlas
pip install -e ".[dev]"
cd ..

# Install Maia (collection service)
cd maia
pip install -e ".[dev]"
cd ..

# Install Alkyone (testing)
cd alkyone
pip install -e .
cd ..
```

---

## Configuration

### 1. Database Setup

Create a PostgreSQL database:

```bash
createdb pleiades_dev
```

Or use [Neon](https://neon.tech) for serverless PostgreSQL.

### 2. Environment Variables

Copy environment templates:

```bash
cp atlas/ENV.example atlas/.env
cp maia/ENV.example maia/.env
```

Edit `atlas/.env`:

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/pleiades_dev

# Vault (choose one)
VAULT_PROVIDER=huggingface  # or 'gcs'
HF_DATASET_ID=your-username/pleiades-vault
HF_TOKEN=hf_your_token_here

# Environment
ENV=dev
COMPLIANCE_MODE=false
```

Edit `maia/.env`:

```bash
# YouTube API Keys (JSON array)
YOUTUBE_API_KEY_POOL_JSON='["AIzaSy...", "AIzaSy..."]'

# Resiliency Strategy
HYDRA_ENABLED=true
HYDRA_RETRY_ATTEMPTS=3
```

### 3. Initialize Database

```bash
cd atlas
make setup  # Provisions schema
cd ..
```

---

## Running Services

### Maia Hunter (Discovery Agent)

```bash
cd maia
python -m maia.hunter.flow
```

### Maia Tracker (Monitoring Agent)

```bash
cd maia
python -m maia.tracker.flow
```

### Docker Compose (All Services)

```bash
docker-compose up -d
```

---

## Verify Installation

### Run Smoke Tests

```bash
cd alkyone
pytest tests/components/atlas/test_smoke.py
```

Expected output:
```
✓ test_database_connectivity PASSED
✓ test_vault_configuration PASSED
✓ test_api_keys_loaded PASSED
✓ test_configuration_complete PASSED
```

### Check Database

```bash
psql $DATABASE_URL -c "SELECT COUNT(*) FROM search_queue;"
psql $DATABASE_URL -c "SELECT COUNT(*) FROM watchlist;"
```

---

## First Run

### Add Search Queries

```bash
psql $DATABASE_URL << EOF
INSERT INTO search_queue (query_term, priority)
VALUES
  ('machine learning tutorial', 5),
  ('viral cooking videos', 3);
EOF
```

### Run Hunter Cycle

```bash
cd maia
python -m maia.hunter.flow
```

Expected output:
```
INFO - === Starting Hunter Cycle ===
INFO - Fetched 2 search queries
INFO - Discovered 50 videos
INFO - === Hunter Cycle Complete ===
```

### Run Tracker Cycle

```bash
python -m maia.tracker.flow
```

Expected output:
```
INFO - === Starting Tracker Cycle (Adaptive Scheduling) ===
INFO - Fetched 50 videos from watchlist
INFO - ✓ Stored 50 metrics to Vault
INFO - ✓ Updated 50 watchlist schedules
INFO - === Tracker Cycle Complete ===
```

---

## Next Steps

- **[Architecture Guide](architecture.md)** - Understand the system design
- **[Adaptive Scheduling](adaptive-scheduling.md)** - Learn about infinite video tracking
- **[Resiliency Strategy](resiliency-strategy.md)** - Understand API key management
- **[Testing Guide](testing.md)** - Run tests and contribute

---

## Troubleshooting

### Database Connection Error

```
psycopg.OperationalError: could not connect to server
```

**Fix**: Verify `DATABASE_URL` in `.env` and ensure PostgreSQL is running.

### API Key Error

```
ERROR - Failed to fetch data: HTTP 403
```

**Fix**: Verify `YOUTUBE_API_KEY_POOL_JSON` contains valid keys.

### Vault Upload Error

```
ERROR - HF upload failed: Unauthorized
```

**Fix**: Verify `HF_TOKEN` has write access to `HF_DATASET_ID`.

### Import Error

```
ModuleNotFoundError: No module named 'atlas'
```

**Fix**: Install components with `pip install -e .` from their directories.

---

## Getting Help

- **Documentation**: [docs/README.md](README.md)
- **Component Guides**: See `atlas/docs/` and `maia/docs/`
- **Issues**: GitHub Issues
- **Contributing**: [docs/contributing.md](contributing.md)
