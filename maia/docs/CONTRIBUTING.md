# Contributing to Maia

Thank you for your interest in contributing to Maia! This guide will help you maintain consistency with the Pleiades architecture and Atlas infrastructure.

## Prerequisites

Before contributing, familiarize yourself with:
1. **Atlas Documentation**: Read `atlas/docs/` to understand the infrastructure layer
2. **Architecture**: Review `ARCHITECTURE.md` for Maia's design principles
3. **Project Pleiades**: Understand the distributed system context

## Development Setup

### 1. Clone and Install

```bash
# Clone repository
git clone <repository-url>
cd pleiades

# Install Atlas (required dependency)
cd atlas
pip install -e ".[all,dev]"

# Install Maia
cd ../maia
pip install -e ".[dev]"
```

### 2. Environment Configuration

```bash
# Copy environment template
cp ENV.example .env

# Configure required variables
# See ENV.example for details
```

### 3. Verify Installation

```bash
# Run tests
pytest

# Check imports
python -c "from maia import __version__; print(__version__)"
python -c "from atlas.adapters.maia import MaiaDAO; print('OK')"
```

## Development Workflow

### 1. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes

Follow these guidelines:

#### Code Style
- **Formatter**: Black (line length: 100)
- **Import sorting**: isort (black-compatible profile)
- **Type hints**: Required for all functions (strict mode)

```bash
# Format code
make format

# Check formatting
make lint
```

#### Type Safety
All functions must have complete type hints:

```python
from typing import Dict, Any, List, Optional

async def my_function(arg: str, optional: Optional[int] = None) -> Dict[str, Any]:
    """Docstring describing the function."""
    result: Dict[str, Any] = {}
    return result
```

Run type checking:
```bash
make type-check
```

#### Documentation
- Add docstrings to all public functions
- Use Google-style docstrings
- Update README.md if adding user-facing features
- Update ARCHITECTURE.md for architectural changes

```python
async def fetch_data(batch_size: int) -> List[Dict[str, Any]]:
    """
    Fetch a batch of data from the database.
    
    Args:
        batch_size: Number of items to fetch
        
    Returns:
        List of data dictionaries
        
    Raises:
        DatabaseError: If connection fails
    """
    pass
```

### 3. Add Tests

All new code must have tests:

#### Test Organization
- `tests/test_hunter.py` - Hunter unit tests
- `tests/test_tracker.py` - Tracker unit tests
- `tests/test_integration.py` - End-to-end flow tests (mark with `@pytest.mark.integration`)
- `tests/test_validation.py` - Edge cases and validation tests

#### Test Standards
```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_my_feature():
    """Test description explaining what is being verified."""
    with patch("maia.hunter.flow.MaiaDAO") as MockDAO:
        mock_dao = MockDAO.return_value
        mock_dao.my_method = AsyncMock(return_value=expected_value)
        
        result = await my_function()
        
        assert result == expected_value
        mock_dao.my_method.assert_called_once()
```

#### Run Tests
```bash
# Run all tests
make test

# Run specific test file
pytest tests/test_hunter.py -v

# Run with coverage
pytest --cov=maia --cov-report=html
```

### 4. Commit Guidelines

Follow conventional commits:

```
type(scope): short description

Longer description if needed.

Closes #123
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Maintenance tasks

Examples:
```
feat(hunter): add support for custom date ranges

fix(tracker): handle deleted videos gracefully

docs: update ARCHITECTURE.md with 3-Zone Defense details

test(integration): add Resiliency Strategy verification tests
```

## Core Rules

### 1. The DAO Pattern

**NEVER write raw SQL in Maia. All database access through MaiaDAO.**

```python
# ✅ CORRECT
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()
batch = await dao.fetch_hunter_batch(10)

# ❌ WRONG
from atlas.db import db

async with db.get_connection() as conn:
    await conn.execute("SELECT * FROM search_queue")  # NO!
```

If you need a new database operation:
1. Add the method to `atlas/src/atlas/adapters/maia.py`
2. Implement it using the `DatabaseAdapter` base class methods
3. Add tests in `atlas/tests/test_maia_adapter.py`
4. Use it in Maia

### 2. Statelessness

**Maia must never persist local state.**

```python
# ❌ WRONG - Local caching
cache = {}  # Don't do this

def get_data():
    if key in cache:
        return cache[key]
    # ...

# ✅ CORRECT - Always query Atlas
async def get_data():
    dao = MaiaDAO()
    return await dao.fetch_data()
```

### 3. Resiliency Strategy

**Rate limits (429) must trigger immediate termination.**

```python
if resp.status == 429:
    logger.critical("HIT 429 RATE LIMIT. INITIATING CHURN & BURN.")
    raise SystemExit("429 Rate Limit - Container Suicide")

# In flows, DON'T catch SystemExit
try:
    await search_youtube(topic)
except SystemExit:
    # Log but MUST re-raise
    logger.critical("Resiliency Strategy activated")
    raise  # Critical - must propagate
except Exception as e:
    # Other errors can be handled normally
    logger.error(f"Error: {e}")
```

### 4. Configuration

**Use atlas.config.settings exclusively.**

```python
# ✅ CORRECT
from atlas.config import settings
from atlas.utils import KeyRing

hunter_keys = KeyRing("hunting")
api_keys = hunter_keys.next_key()

# ❌ WRONG - Don't create separate config
import os
API_KEY = os.getenv("YOUTUBE_API_KEY")  # NO!
```

### 5. Import Standards

```python
# Prefect
from prefect import flow, task, get_run_logger

# Atlas - Always import from adapters
from atlas.adapters.maia import MaiaDAO
from atlas.utils import KeyRing
from atlas import vault

# Standard library
import asyncio
import logging
from typing import Dict, Any, List, Optional

# Third-party
import aiohttp
```

## Common Patterns

### Adding a New Hunter Feature

1. Add database method to `MaiaDAO` if needed
2. Create task function with Prefect decorator
3. Add type hints and docstring
4. Implement error handling (Resiliency Strategy)
5. Add unit tests
6. Add integration test
7. Update documentation

### Adding a New Tracker Zone

1. Update `fetch_tracker_targets()` query in `MaiaDAO`
2. Document zone strategy in docstring
3. Add tests verifying priority ordering
4. Update ARCHITECTURE.md

### Extending KeyRing Functionality

**Don't modify Maia** - KeyRing is in Atlas:
1. Update `atlas/src/atlas/utils.py`
2. Add tests in `atlas/tests/test_utils.py`
3. Update Atlas documentation
4. Use new functionality in Maia

## Pull Request Process

### 1. Pre-submission Checklist

- [ ] Code formatted with Black and isort
- [ ] Type hints added (passes mypy strict mode)
- [ ] Tests added and passing
- [ ] Documentation updated
- [ ] No raw SQL in Maia (DAO pattern followed)
- [ ] Resiliency Strategy respected
- [ ] No local state persistence
- [ ] Imports follow standards
- [ ] Commit messages follow conventions

### 2. Submit PR

```bash
git push origin feature/your-feature-name
```

Create pull request with:
- Clear title and description
- Reference to related issues
- Screenshots/examples if applicable
- Breaking changes highlighted

### 3. Review Process

PRs must pass:
- Automated tests (pytest)
- Type checking (mypy)
- Code style (black, isort)
- Manual review by maintainers

### 4. Merge

- Squash commits if multiple small commits
- Use conventional commit message for merge
- Delete branch after merge

## Testing Philosophy

### Unit Tests (Fast, Mocked)

Mock all external dependencies:
- Database (MaiaDAO)
- HTTP calls (aiohttp.ClientSession)
- Storage (atlas.vault)

Goal: Test business logic in isolation

### Integration Tests (Slow, Real Services)

Mark with `@pytest.mark.integration`:
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_e2e():
    """End-to-end Hunter cycle test."""
    # Test with mocked external APIs but real MaiaDAO logic
```

### Run Tests Selectively

```bash
# Fast unit tests only
pytest -m "not integration"

# Integration tests only
pytest -m integration

# Specific test
pytest tests/test_hunter.py::test_fetch_batch -v
```

## Debugging Tips

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or run with:
```bash
python -m maia hunter --log-level DEBUG
```

### Use Prefect UI

```bash
# Start local Prefect server
prefect server start

# View flows at http://localhost:4200
```

### Inspect Database State

```python
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()
batch = await dao.fetch_hunter_batch(10)
print(batch)
```

## Common Issues

### Import Errors

```bash
# Ensure Atlas is installed
cd atlas && pip install -e .

# Ensure Maia can find Atlas
export PYTHONPATH="${PYTHONPATH}:$(pwd)/atlas/src"
```

### Type Checking Failures

```bash
# Run mypy on specific file
mypy src/maia/hunter.py --strict

# Common fixes:
# - Add return type hints
# - Annotate variables with complex types
# - Use Optional[T] for nullable values
```

### Test Failures

```bash
# Run with verbose output
pytest tests/test_hunter.py -vv

# Run single test
pytest tests/test_hunter.py::test_fetch_batch -vv

# Use pdb for debugging
pytest --pdb
```

## Questions?

- **Atlas Issues**: Check `atlas/docs/`
- **Maia Architecture**: Review `ARCHITECTURE.md`
- **General Questions**: Open a discussion on GitHub

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

