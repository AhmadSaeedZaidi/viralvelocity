# Testing Guide

**Comprehensive testing strategy for Pleiades**

---

## Overview

Pleiades uses a three-tier testing strategy:

1. **Unit Tests** - Component-specific tests in `{component}/tests/`
2. **Integration Tests** - Cross-component tests in `alkyone/tests/`
3. **Smoke Tests** - Live service connectivity tests in `alkyone/tests/`

---

## Test Organization

```
pleiades/
├── atlas/tests/          # Atlas unit tests
│   ├── test_db.py
│   ├── test_vault.py
│   ├── test_config.py
│   └── test_adapters.py
│
├── maia/tests/           # Maia unit tests
│   ├── test_hunter.py
│   ├── test_tracker.py
│   └── test_janitor.py
│
└── alkyone/tests/        # Integration & smoke tests
    └── components/
        ├── atlas/
        │   └── test_smoke.py
        └── maia/
            ├── test_integration.py
            └── test_validation.py
```

---

## Running Tests

### All Tests

```bash
# From project root
pytest

# With coverage
pytest --cov=atlas --cov=maia --cov-report=html
```

### Component-Specific

```bash
# Atlas tests only
cd atlas
pytest tests/

# Maia tests only
cd maia
pytest tests/

# Integration tests only
cd alkyone
pytest tests/
```

### By Marker

```bash
# Integration tests only
pytest -m integration

# Smoke tests only
pytest -m smoke

# Skip slow tests
pytest -m "not slow"
```

### Specific Test File

```bash
pytest alkyone/tests/components/maia/test_integration.py
pytest atlas/tests/test_db.py::test_connection_pool
```

---

## Unit Tests

### Atlas Unit Tests

Test individual Atlas modules in isolation:

```python
# atlas/tests/test_db.py
import pytest
from atlas import db

@pytest.mark.asyncio
async def test_connection_pool():
    """Test database connection pool initialization."""
    await db.initialize()
    
    async with db.get_connection() as conn:
        result = await conn.fetchrow("SELECT 1 as num")
        assert result["num"] == 1
    
    await db.close()
```

**Run**:
```bash
cd atlas
pytest tests/test_db.py
```

### Maia Unit Tests

Test individual Maia agents in isolation (with mocked dependencies):

```python
# maia/tests/test_hunter.py
import pytest
from unittest.mock import AsyncMock, patch
from maia.hunter import run_hunter_cycle

@pytest.mark.asyncio
async def test_hunter_cycle():
    """Test Hunter cycle with mocked DAO."""
    with patch("maia.hunter.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(return_value=[])
        
        stats = await run_hunter_cycle(batch_size=10)
        
        assert stats["queries_processed"] == 0
        mock_dao.fetch_hunter_batch.assert_called_once_with(10)
```

**Run**:
```bash
cd maia
pytest tests/test_hunter.py
```

---

## Integration Tests

### Location

All integration tests live in `alkyone/tests/components/`:

```
alkyone/tests/components/
├── atlas/
│   └── test_smoke.py         # Atlas connectivity tests
└── maia/
    ├── test_integration.py   # End-to-end Maia flows
    └── test_validation.py    # Edge cases & validation
```

### Integration Test Example

```python
# alkyone/tests/components/maia/test_integration.py
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_cycle_complete_flow():
    """Test complete Hunter cycle from fetch to ingest."""
    with patch("maia.hunter.MaiaDAO") as MockDAO, \
         patch("maia.hunter.vault") as mock_vault, \
         patch("maia.hunter.aiohttp.ClientSession") as MockSession:
        
        # Setup mocks
        mock_dao = MockDAO.return_value
        mock_dao.fetch_hunter_batch = AsyncMock(return_value=[{
            "id": 1,
            "query_term": "test query",
            "next_page_token": None
        }])
        mock_dao.ingest_video_metadata = AsyncMock()
        
        # Mock API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "items": [{"id": {"videoId": "TEST123"}, "snippet": {...}}]
        })
        
        mock_session = MockSession.return_value.__aenter__.return_value
        mock_session.get.return_value.__aenter__.return_value = mock_response
        
        # Execute
        stats = await run_hunter_cycle(batch_size=1)
        
        # Verify
        assert stats["queries_processed"] == 1
        assert stats["videos_discovered"] == 1
        mock_dao.ingest_video_metadata.assert_called_once()
```

**Run**:
```bash
cd alkyone
pytest tests/components/maia/test_integration.py
```

---

## Smoke Tests

### Purpose

Verify connectivity to live external services:
- Database (PostgreSQL/Neon)
- Vault (HuggingFace/GCS)
- API endpoints (YouTube)

### Smoke Test Example

```python
# alkyone/tests/components/atlas/test_smoke.py
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_database_connectivity():
    """Verify actual database connection."""
    is_healthy = await db.health_check()
    assert is_healthy, "Database health check failed"
    await db.close()

@pytest.mark.smoke
def test_vault_configuration():
    """Verify vault provider is configured."""
    assert settings.VAULT_PROVIDER in ["huggingface", "gcs"]
    assert vault is not None
```

**Run**:
```bash
cd alkyone
pytest -m smoke
```

---

## Test Fixtures

### Shared Fixtures (Alkyone)

Common fixtures in `alkyone/src/alkyone/fixtures.py`:

```python
@pytest_asyncio.fixture(scope="session")
async def system_init():
    """Initialize database for test suite."""
    await db.initialize()
    yield
    await db.close()

@pytest_asyncio.fixture(scope="function")
async def fresh_db(system_init):
    """Wipe and re-provision DB schema for each test."""
    async with db.get_connection() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        
        # Load schema
        schema_path = os.path.join(os.path.dirname(atlas.schema.__file__), "schema.sql")
        with open(schema_path, "r") as f:
            await conn.execute(f.read())
    
    yield
```

### Using Fixtures

```python
def test_with_fresh_db(fresh_db):
    """Test with clean database state."""
    # Database is clean slate
    # ... test implementation
```

---

## Test Markers

### Standard Markers

```python
@pytest.mark.asyncio         # Async test
@pytest.mark.integration     # Integration test (may be slow)
@pytest.mark.smoke           # Smoke test (requires live services)
@pytest.mark.slow            # Slow test (>1 second)
```

### Configuration

Define in `pytest.ini`:

```ini
[pytest]
markers =
    integration: Integration tests
    smoke: Smoke tests requiring live services
    slow: Slow-running tests
    
asyncio_mode = auto
```

---

## Best Practices

### 1. Mock External Services

```python
# ✅ GOOD - Mock YouTube API
with patch("maia.hunter.aiohttp.ClientSession") as MockSession:
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"items": []})
    
    mock_session = MockSession.return_value.__aenter__.return_value
    mock_session.get.return_value.__aenter__.return_value = mock_response
    
    # Test code

# ❌ BAD - Hit real API
response = await session.get("https://youtube.googleapis.com/...")
```

### 2. Test Behavior, Not Implementation

```python
# ✅ GOOD - Test behavior
async def test_hunter_discovers_videos():
    result = await run_hunter_cycle()
    assert result["videos_discovered"] > 0

# ❌ BAD - Test implementation details
async def test_hunter_calls_dao_method():
    # Don't test internal method calls
    mock_dao.some_internal_method.assert_called()
```

### 3. Use Descriptive Names

```python
# ✅ GOOD
async def test_tracker_handles_deleted_videos_gracefully()

# ❌ BAD
async def test_tracker_edge_case()
```

### 4. Document Test Purpose

```python
@pytest.mark.integration
async def test_hydra_protocol_on_rate_limit():
    """
    Verify that Hunter raises SystemExit when encountering
    429 rate limit, triggering Hydra Protocol for key rotation.
    """
    # Test implementation
```

### 5. Keep Tests Independent

```python
# ✅ GOOD - Each test is independent
async def test_a(fresh_db):
    # Test A logic
    
async def test_b(fresh_db):
    # Test B logic (doesn't depend on test_a)

# ❌ BAD - Tests depend on order
async def test_create_video():
    global video_id
    video_id = await dao.create_video()

async def test_update_video():
    await dao.update_video(video_id)  # Depends on previous test!
```

---

## Coverage

### Generate Coverage Report

```bash
# HTML report
pytest --cov=atlas --cov=maia --cov-report=html
open htmlcov/index.html

# Terminal report
pytest --cov=atlas --cov=maia --cov-report=term

# Minimum coverage threshold
pytest --cov=atlas --cov=maia --cov-fail-under=80
```

### Target Coverage

- **Atlas**: 80%+ (core infrastructure)
- **Maia**: 70%+ (agents with external dependencies)
- **Alkyone**: N/A (test suite)

---

## Continuous Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v2
    
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        cd atlas && pip install -e ".[dev]"
        cd ../maia && pip install -e ".[dev]"
        cd ../alkyone && pip install -e .
    
    - name: Run tests
      run: |
        pytest --cov=atlas --cov=maia --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v2
```

---

## Troubleshooting

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'atlas'`

**Solution**:
```bash
cd atlas && pip install -e .
cd ../maia && pip install -e .
cd ../alkyone && pip install -e .
```

### Fixture Not Found

**Problem**: `fixture 'fresh_db' not found`

**Solution**: Import fixtures from alkyone:
```python
from alkyone.fixtures import fresh_db
```

### Async Tests Hanging

**Problem**: Test hangs indefinitely

**Solution**: Ensure `@pytest.mark.asyncio` is present:
```python
@pytest.mark.asyncio  # ← Don't forget!
async def test_async_function():
    ...
```

### Database Connection Errors

**Problem**: `psycopg.OperationalError: could not connect`

**Solution**: Verify `DATABASE_URL` in `.env`:
```bash
echo $DATABASE_URL
# Should output: postgresql://user:pass@host:5432/db
```

---

## Summary

Pleiades testing strategy:

- ✅ **Unit tests** in component directories
- ✅ **Integration tests** in alkyone/
- ✅ **Smoke tests** for live services
- ✅ **Mocked external dependencies**
- ✅ **Comprehensive coverage** (70-80%+)
- ✅ **CI/CD integration**

**Result**: Reliable, maintainable codebase with fast feedback loops.
