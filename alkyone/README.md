# Alkyone - Integration Testing Suite

**Alkyone** is the dedicated integration and system testing module for Project Pleiades. It contains all integration tests, smoke tests, and system-level validation for Atlas, Maia, and other Pleiades components.

## Purpose

Alkyone separates integration/system tests from unit tests to maintain clean separation of concerns:

- **Unit Tests** remain in component directories (`atlas/tests/`, `maia/tests/`)
- **Integration Tests** live here in Alkyone
- **Smoke Tests** for verifying live service connectivity
- **System Tests** for end-to-end validation

## Structure

```
alkyone/
├── src/alkyone/
│   └── fixtures.py          # Shared test fixtures and utilities
└── tests/
    └── components/
        ├── atlas/
        │   └── test_smoke.py       # Atlas smoke tests
        └── maia/
            ├── test_integration.py  # Maia integration tests
            └── test_validation.py   # Maia validation tests
```

## Test Categories

### Integration Tests
Tests that verify interactions between components or with external services (mocked).

**Location**: `tests/components/{component}/test_integration.py`

**Examples**:
- End-to-end Hunter cycle (fetch → search → ingest)
- End-to-end Tracker cycle (fetch → update)
- Hydra Protocol verification
- Multi-component workflows

### Smoke Tests
Tests that verify connectivity to live external services.

**Location**: `tests/components/{component}/test_smoke.py`

**Examples**:
- Database connectivity
- Vault provider connectivity
- API endpoint availability

### Validation Tests
Tests for edge cases, error handling, and data validation.

**Location**: `tests/components/{component}/test_validation.py`

**Examples**:
- Missing/invalid data handling
- Network error scenarios
- Partial failure handling
- Boundary conditions

## Running Tests

### All Integration Tests
```bash
cd alkyone
pytest tests/
```

### Component-Specific Tests
```bash
# Atlas integration tests
pytest tests/components/atlas/

# Maia integration tests
pytest tests/components/maia/
```

### Specific Test Categories
```bash
# Integration tests only
pytest -m integration

# Smoke tests only
pytest -m smoke

# Validation tests
pytest tests/components/maia/test_validation.py
```

### With Coverage
```bash
pytest tests/ --cov=alkyone --cov-report=html
```

## Test Markers

Alkyone uses pytest markers to categorize tests:

- `@pytest.mark.integration` - Integration tests (may be slow)
- `@pytest.mark.smoke` - Smoke tests (require live services)
- `@pytest.mark.slow` - Tests that take significant time

## Configuration

### Environment Variables
Tests use the same environment variables as the components they test:

```bash
# Copy from component root
cp ../atlas/.env .env
# or
cp ../maia/.env .env
```

### Test Configuration
See `pytest.ini` in component directories for test-specific configuration.

## Writing Tests

### Integration Test Template

```python
"""
Integration tests for [Component] [Feature].
"""
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.integration
@pytest.mark.asyncio
async def test_feature_end_to_end():
    """Test complete workflow from start to finish."""
    # Setup mocks for external services
    with patch("component.external_service") as mock_service:
        mock_service.return_value = expected_value
        
        # Execute workflow
        result = await component.run_workflow()
        
        # Verify results
        assert result["status"] == "success"
        mock_service.assert_called_once()
```

### Smoke Test Template

```python
"""
Smoke tests for [Component] connectivity.
"""
import pytest

@pytest.mark.smoke
@pytest.mark.asyncio
async def test_service_connectivity():
    """Verify connection to external service."""
    # Attempt connection
    result = await service.health_check()
    
    # Verify connectivity
    assert result is True
```

## Best Practices

### 1. Mock External Services
Integration tests should mock external APIs to avoid:
- Rate limits
- Network dependencies
- Quota consumption
- Flaky tests

### 2. Use Fixtures
Leverage shared fixtures from `src/alkyone/fixtures.py`:

```python
from alkyone.fixtures import mock_youtube_response

def test_with_fixture(mock_youtube_response):
    # Use fixture
    assert "items" in mock_youtube_response
```

### 3. Test Real Behavior
Integration tests should test actual component behavior, not implementation details:

```python
# ✅ GOOD - Tests behavior
async def test_hunter_discovers_videos():
    result = await run_hunter_cycle()
    assert result["videos_discovered"] > 0

# ❌ BAD - Tests implementation
async def test_hunter_calls_dao():
    # Don't test internal method calls
    pass
```

### 4. Descriptive Names
Use clear, descriptive test names:

```python
# ✅ GOOD
async def test_tracker_handles_deleted_videos_gracefully()

# ❌ BAD
async def test_tracker_edge_case()
```

### 5. Document Test Purpose
Every test should have a docstring explaining what it validates:

```python
@pytest.mark.integration
async def test_hydra_protocol_on_rate_limit():
    """
    Verify that Hunter raises SystemExit when encountering
    429 rate limit, triggering Hydra Protocol for IP rotation.
    """
    # Test implementation
```

## Component Guidelines

### Atlas Integration Tests
- Database operations with mocked connections
- Vault operations with mocked storage
- Event bus functionality
- Notification system

### Maia Integration Tests
- Complete Hunter/Tracker cycles
- Hydra Protocol verification
- Key rotation behavior
- Snowball effect validation

## Continuous Integration

Alkyone tests run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run Integration Tests
  run: |
    cd alkyone
    pytest tests/ -v
```

### Test Isolation
Each test should:
- Be independent (no shared state)
- Clean up after itself
- Not depend on test execution order
- Be idempotent (repeatable)

## Troubleshooting

### Import Errors
Ensure components are installed:
```bash
cd ../atlas && pip install -e .
cd ../maia && pip install -e .
```

### Fixture Not Found
Import fixtures from alkyone:
```python
from alkyone.fixtures import your_fixture
```

### Slow Tests
Use markers to skip slow tests during development:
```bash
pytest -m "not slow"
```

## Contributing

When adding new features to Atlas or Maia:

1. **Add unit tests** to component's `tests/` directory
2. **Add integration tests** to `alkyone/tests/components/{component}/`
3. **Update fixtures** in `src/alkyone/fixtures.py` if needed
4. **Mark tests appropriately** (`@pytest.mark.integration`, etc.)

## Future Enhancements

- [ ] Performance benchmarks
- [ ] Load testing suite
- [ ] Chaos engineering tests
- [ ] Security testing
- [ ] Contract testing between components

---

**Version**: 0.1.0  
**Last Updated**: 2026-01-10  
**Maintainer**: Ahmad Saeed Zaidi  
**License**: MIT



