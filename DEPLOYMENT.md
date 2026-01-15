# Pleiades Deployment Guide

**Production deployment checklist for the Pleiades platform**

---

## Pre-Deployment Checklist

### Code Quality ✅
- [x] All files compile without errors
- [x] Type hints complete
- [x] Linters pass (black, isort, mypy)
- [x] No redundant documentation

### Testing ✅
- [x] Unit tests pass
- [x] Integration tests added (Ghost Tracking, Janitor)
- [x] All critical paths covered
- [x] Error handling validated

### Documentation ✅
- [x] Architecture documented
- [x] API references complete
- [x] No redundancy
- [x] Component docs streamlined

---

## GitHub Workflows

### Platform CI/CD (New)

**`.github/workflows/ci.yml`** - Hydra CI (The Gatekeeper)
- Runs on all branches
- Linting & type checking (black, isort, mypy)
- Unit tests (mocked, fast)
- Integration tests (with PostgreSQL)

**`.github/workflows/cd.yml`** - Hydra CD (The Builder & Runner)
- Builds Docker images on `main` push
- Deploys agents on schedule (every 4 hours)
- Manual deployment via `workflow_dispatch`
- Hydra Protocol exit code handling

### ML Infrastructure (Existing)

**`.github/workflows/ml-ci.yml`** - ML Linting & Testing
- HF Spaces (model-api, ml-dashboard)
- Training pipeline validation

**`.github/workflows/ml-cd.yml`** - HF Spaces Deployment
- Deploys to Hugging Face Spaces
- API and Dashboard sync

---

## Required GitHub Secrets

### Platform Secrets
```bash
DATABASE_URL              # PostgreSQL connection string (production)
NEON_API_KEY             # For ephemeral CI test databases
VAULT_PROVIDER            # "huggingface" or "gcs"
HF_TOKEN                  # HuggingFace API token
HF_DATASET_ID            # username/dataset-name
YOUTUBE_API_KEY_POOL_JSON # ["key1", "key2", "key3"]
```

### Optional Secrets
```bash
DISCORD_WEBHOOK_ALERTS    # Alert notifications
GCS_BUCKET_NAME          # If using GCS vault
PREFECT_API_URL          # If using Prefect Cloud
PREFECT_API_KEY          # Prefect authentication
```

## Required GitHub Variables

```bash
NEON_PROJECT_ID          # For ephemeral CI test databases
```

---

## Deployment Steps

### 1. Configure Secrets & Variables

Go to GitHub Settings → Secrets and variables → Actions:

```bash
# Required Secrets
gh secret set DATABASE_URL --body "postgresql://user:pass@host:5432/db"
gh secret set NEON_API_KEY  # Get from console.neon.tech → Account → API Keys
gh secret set VAULT_PROVIDER --body "huggingface"
gh secret set HF_TOKEN --body "hf_xxxxxxxxxxxxx"
gh secret set HF_DATASET_ID --body "username/pleiades-vault"
gh secret set YOUTUBE_API_KEY_POOL_JSON --body '["key1","key2","key3"]'

# Required Variables
gh variable set NEON_PROJECT_ID --body "your-neon-project-id"

# Optional
gh secret set DISCORD_WEBHOOK_ALERTS --body "https://discord.com/api/webhooks/..."
```

**Note**: CI uses Neon ephemeral databases for integration tests (requires `NEON_API_KEY` and `NEON_PROJECT_ID`).

### 2. Push to GitHub

```bash
# Add all changes
git add .

# Commit with descriptive message
git commit -m "feat: integrate Ghost Tracking, hot/cold storage, and CI/CD"

# Push to main (triggers CI)
git push origin main
```

### 3. Monitor CI Pipeline

Watch the CI workflow:
```
https://github.com/your-org/pleiades/actions
```

**Expected stages**:
1. ✅ Linting & Type Check (~2 min)
2. ✅ Unit Tests (~3 min)
3. ✅ Integration Tests (~5 min)
4. ✅ Build Docker Image (~3 min)

Total: ~13 minutes

### 4. Verify CD Deployment

The CD workflow will:
1. Build Maia Docker image
2. Push to GitHub Container Registry
3. Run on schedule (every 4 hours)

**First manual run**:
```
GitHub Actions → Hydra CD → Run workflow → Select agent (hunter) → Run
```

### 5. Monitor Agent Execution

Check logs in GitHub Actions:

**Healthy run**:
```
Hunter: Starting cycle (batch_size=10)
KeyRing: Initialized 'hunting' with 3 keys
Hunter: Discovered 42 videos
Hunter: Added 42 to watchlist
Hunter: Cycle complete (duration=12.3s)
Agent exited cleanly (exit 0)
```

**Quota exhausted (expected)**:
```
🔥 HYDRA PROTOCOL: All keys exhausted for hunter
Initiating clean container termination (exit 0)
Agent exited cleanly (Hydra Protocol: quota exceeded)
```

---

## Agent Deployment Schedule

### Automatic (Cron)

**CD runs every 4 hours**:
- 00:00 UTC
- 04:00 UTC
- 08:00 UTC
- 12:00 UTC
- 16:00 UTC
- 20:00 UTC

### Manual Deployment

```bash
# Via GitHub UI
Actions → Hydra CD → Run workflow → Select agent → Run

# Via gh CLI
gh workflow run cd.yml -f agent=hunter -f dry_run=false
gh workflow run cd.yml -f agent=tracker -f dry_run=false
gh workflow run cd.yml -f agent=janitor -f dry_run=false
```

---

## Monitoring & Alerts

### Key Metrics

**Database Size**:
```sql
SELECT pg_size_pretty(pg_database_size('pleiades'));
-- Should stay under 500 MB
```

**Hot Tier Stats**:
```sql
SELECT COUNT(*) FROM video_stats_log;
-- Should be ~2.8M rows (7 days × 100k videos/day × 4 updates/day)
```

**Watchlist Size**:
```sql
SELECT COUNT(*) FROM watchlist;
-- Grows linearly with discovered videos
```

**Vault Usage**:
```bash
# Check Parquet file count
ls vault/metrics/ | wc -l
```

### Health Checks

**Atlas**:
```python
from atlas.utils import health_check_all
result = await health_check_all()
# {"database": true}
```

**Vault**:
```python
from atlas.vault import vault
vault.store_json("test.json", {"status": "ok"})
data = vault.fetch_json("test.json")
# Should return {"status": "ok"}
```

---

## Troubleshooting

### CI Fails: "poetry section not found" or "pleiades-atlas not found"

**Cause**: Atlas uses Poetry, Maia uses setuptools. CI handles this automatically.

**Fix**: Ensure local installation order:
```bash
pip install -e atlas[all]  # Poetry-based (requires poetry-core)
pip install -e maia[dev]    # setuptools-based
```

### CI Fails: "mypy errors"

```bash
# Run mypy locally
cd atlas && mypy src/ --strict
cd maia && mypy src/ --strict

# Fix type issues
```

### CI Fails: "Integration tests timeout"

```bash
# Check PostgreSQL service
docker ps | grep postgres

# Run locally
cd alkyone
pytest tests/ -m integration -v
```

### CD Fails: "Image not found"

```bash
# Verify image was pushed
docker pull ghcr.io/your-org/pleiades-maia:latest

# Check registry permissions
gh api /user/packages/container/pleiades-maia
```

### Agent Exits with Code 1

```bash
# Check logs for actual error
gh run view <run-id> --log

# Common causes:
# - DATABASE_URL incorrect
# - VAULT_PROVIDER misconfigured
# - Missing API keys
```

### Janitor Not Archiving

```bash
# Check Janitor config
echo $JANITOR_ENABLED  # should be "true"
echo $JANITOR_RETENTION_DAYS  # should be "7"

# Run manually with dry-run
python -m maia.janitor --dry-run

# Check vault permissions
```

---

## Scaling

### Horizontal Scaling

Run multiple agent instances:

```yaml
# docker-compose.yml
services:
  hunter-1:
    image: ghcr.io/your-org/pleiades-maia:latest
    command: python -m maia.hunter
    
  hunter-2:
    image: ghcr.io/your-org/pleiades-maia:latest
    command: python -m maia.hunter
    
  hunter-3:
    image: ghcr.io/your-org/pleiades-maia:latest
    command: python -m maia.hunter
```

**Note**: `FOR UPDATE SKIP LOCKED` prevents race conditions.

### Vertical Scaling

Increase batch sizes:

```bash
# Environment variables
HUNTER_BATCH_SIZE=20    # default: 10
TRACKER_BATCH_SIZE=100  # default: 50
SCRIBE_BATCH_SIZE=10    # default: 5
```

### Database Scaling

**Connection pooling**:
```python
# atlas/src/atlas/config.py
DATABASE_POOL_MIN_SIZE=2    # default
DATABASE_POOL_MAX_SIZE=10   # default
```

**Read replicas**:
```python
# For heavy read workloads
DATABASE_READ_URL=postgresql://...
DATABASE_WRITE_URL=postgresql://...
```

---

## Rollback Procedure

### Quick Rollback

```bash
# 1. Stop current deployment
gh workflow run cd.yml --json '{"dry_run": "true"}'

# 2. Deploy previous image
docker pull ghcr.io/your-org/pleiades-maia:sha-<previous-commit>
docker tag ghcr.io/your-org/pleiades-maia:sha-<previous-commit> \
           ghcr.io/your-org/pleiades-maia:latest
docker push ghcr.io/your-org/pleiades-maia:latest

# 3. Restart agents
gh workflow run cd.yml
```

### Git Rollback

```bash
# Revert to previous commit
git revert HEAD
git push origin main

# CI/CD will automatically build and deploy
```

---

## Production Checklist

### Before First Deploy
- [ ] All secrets configured
- [ ] Database initialized (`atlas/src/atlas/schema.sql`)
- [ ] Vault accessible (HF token or GCS credentials)
- [ ] YouTube API keys valid
- [ ] CI passes all tests

### After First Deploy
- [ ] Monitor for 1 hour
- [ ] Check database size
- [ ] Verify Vault writes
- [ ] Check agent logs
- [ ] Validate watchlist growth

### Daily Monitoring
- [ ] Check CI status
- [ ] Review agent logs
- [ ] Monitor database size
- [ ] Verify Janitor runs
- [ ] Check Vault storage

---

## Support

### Documentation
- **Architecture**: [docs/architecture.md](docs/architecture.md)
- **Ghost Tracking**: [docs/ghost-tracking.md](docs/ghost-tracking.md)
- **Hydra Protocol**: [docs/hydra-protocol.md](docs/hydra-protocol.md)
- **Testing**: [docs/testing.md](docs/testing.md)

### Logs
- **CI/CD**: `https://github.com/your-org/pleiades/actions`
- **Agent Logs**: GitHub Actions workflow logs
- **Database**: Check PostgreSQL logs

---

**Version**: 1.0.0  
**Maintainer**: Ahmad Saeed Zaidi  
**Last Updated**: 2026-01-15

---

## Quick Commands Reference

```bash
# Deploy specific agent
gh workflow run cd.yml -f agent=hunter

# View latest CI run
gh run list --workflow=ci.yml --limit 1

# View logs for latest run
gh run view --log

# List all secrets
gh secret list

# Test locally
docker-compose up -d

# Stop all
docker-compose down
```

---

**Ready for deployment! 🚀**
