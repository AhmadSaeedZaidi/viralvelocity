# Contributing Guide

**Development workflow and standards for Pleiades**

---

## Getting Started

### 1. Set Up Development Environment

```bash
# Clone repository
git clone https://github.com/yourusername/pleiades.git
cd pleiades

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install all components in editable mode
cd atlas && pip install -e ".[dev]" && cd ..
cd maia && pip install -e ".[dev]" && cd ..
cd alkyone && pip install -e . && cd ..
```

### 2. Configure Environment

```bash
# Copy environment templates
cp atlas/ENV.example atlas/.env
cp maia/ENV.example maia/.env

# Edit .env files with your credentials
```

### 3. Initialize Database

```bash
cd atlas
make setup  # Provisions schema
make smoke-test  # Verify connectivity
cd ..
```

---

## Development Workflow

### 1. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes

Follow our coding standards (see below).

### 3. Run Tests

```bash
# Unit tests
cd atlas && pytest tests/
cd maia && pytest tests/

# Integration tests
cd alkyone && pytest tests/

# With coverage
pytest --cov=atlas --cov=maia --cov-report=term
```

### 4. Format & Lint

```bash
# Format code
cd atlas && make format
cd maia && make format

# Check lint
cd atlas && make lint
cd maia && make lint

# Type check
cd atlas && make type-check
cd maia && make type-check
```

### 5. Commit Changes

```bash
git add .
git commit -m "feat: add feature description"
```

**Commit Message Format**:
```
<type>: <description>

[optional body]

[optional footer]
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting)
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Maintenance tasks

### 6. Push & Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

---

## Coding Standards

### Python Style

We use **Black** for formatting and **isort** for import sorting:

```bash
# Auto-format
black src/ tests/

# Sort imports
isort src/ tests/

# Or use Makefile
make format
```

### Type Hints

Use type hints for all function signatures:

```python
# ✅ GOOD
async def fetch_videos(
    batch_size: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    ...

# ❌ BAD
async def fetch_videos(batch_size=50, offset=0):
    ...
```

### Docstrings

Use Google-style docstrings:

```python
def calculate_score(views: int, likes: int) -> float:
    """
    Calculate viral score based on views and likes.
    
    Args:
        views: Total view count
        likes: Total like count
    
    Returns:
        Viral score between 0 and 1
    
    Raises:
        ValueError: If views or likes are negative
    """
    if views < 0 or likes < 0:
        raise ValueError("Views and likes must be non-negative")
    
    return likes / max(views, 1)
```

### Imports

Organize imports in this order:

```python
# 1. Standard library
import logging
from datetime import datetime
from typing import Any, Dict, List

# 2. Third-party
import aiohttp
from prefect import flow, task

# 3. Local
from atlas import db, vault
from atlas.adapters.maia import MaiaDAO
```

### Error Handling

```python
# ✅ GOOD - Specific exceptions
try:
    result = await dao.fetch_videos()
except psycopg.OperationalError as e:
    logger.error(f"Database connection failed: {e}")
    raise
except ValueError as e:
    logger.warning(f"Invalid data: {e}")
    return None

# ❌ BAD - Bare except
try:
    result = await dao.fetch_videos()
except:
    pass
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

# Use appropriate log levels
logger.debug("Detailed debugging information")
logger.info("Normal operation information")
logger.warning("Warning: potential issue")
logger.error("Error occurred")
logger.critical("Critical failure")

# Include context
logger.info(f"Processed {count} videos in {duration:.2f}s")
```

---

## Architecture Patterns

### 1. DAO Pattern

All database operations go through Data Access Objects:

```python
# ✅ GOOD
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()
videos = await dao.fetch_hunter_batch(10)

# ❌ BAD - Direct SQL in agents
async with db.get_connection() as conn:
    result = await conn.fetch("SELECT * FROM videos")
```

### 2. Stateless Agents

Agents should be stateless and idempotent:

```python
# ✅ GOOD - Stateless
@flow(name="run_hunter_cycle")
async def run_hunter_cycle(batch_size: int = 10):
    dao = MaiaDAO()  # New instance each cycle
    queries = await dao.fetch_hunter_batch(batch_size)
    # ... processing

# ❌ BAD - Stateful
class Hunter:
    def __init__(self):
        self.processed_count = 0  # Avoid state!
    
    async def run(self):
        self.processed_count += 1  # Don't track state!
```

### 3. Observer Pattern

Use events for loose coupling:

```python
# Component A emits
from atlas import events

await events.emit("video.discovered", {
    "video_id": video_id,
    "title": title
})

# Component B reacts
@events.on("video.discovered")
async def on_video_discovered(data: dict):
    logger.info(f"New video: {data['video_id']}")
```

### 4. Hydra Protocol

Use HydraExecutor for all external API calls:

```python
from atlas.utils import KeyRing, HydraExecutor

keys = KeyRing("hunting")
executor = HydraExecutor(keys, agent_name="hunter")

async def make_request(api_key: str):
    # API call logic
    ...

result = await executor.execute_async(make_request)
```

---

## Testing Requirements

### Unit Tests

- Add unit tests for all new functions/methods
- Mock external dependencies (API, database)
- Test edge cases and error conditions

```python
@pytest.mark.asyncio
async def test_calculate_score():
    """Test viral score calculation."""
    score = calculate_score(views=1000, likes=100)
    assert 0 <= score <= 1
    
    # Test edge case
    score_zero_views = calculate_score(views=0, likes=0)
    assert score_zero_views == 0
```

### Integration Tests

- Add integration tests for new workflows
- Place in `alkyone/tests/components/{component}/`
- Use `@pytest.mark.integration` marker

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_complete_flow():
    """Test complete Hunter workflow."""
    stats = await run_hunter_cycle(batch_size=1)
    assert stats["queries_processed"] >= 0
```

### Coverage

- Maintain 70%+ coverage for new code
- Run coverage reports locally before PR

```bash
pytest --cov=atlas --cov=maia --cov-report=term
```

---

## Pull Request Process

### 1. PR Title

Use conventional commit format:

```
feat: add Ghost Tracking for infinite video monitoring
fix: resolve Hydra Protocol exit code handling
docs: update quickstart guide
```

### 2. PR Description

Include:

```markdown
## What

Brief description of changes.

## Why

Explanation of motivation and context.

## How

Technical approach and key changes.

## Testing

- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Smoke tests pass
- [ ] Coverage maintained/improved

## Checklist

- [ ] Code follows style guidelines
- [ ] Tests pass locally
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
```

### 3. Review Process

- At least one approval required
- All CI checks must pass
- Address review comments
- Squash commits before merge (optional)

---

## Project Structure

### Adding New Agent to Maia

```bash
# 1. Create agent directory
mkdir maia/src/maia/my_agent

# 2. Create flow module
cat > maia/src/maia/my_agent/flow.py << 'EOF'
"""
My Agent - Description.
"""
import logging
from prefect import flow, task

logger = logging.getLogger(__name__)

@task(name="my_task")
async def my_task():
    """Task description."""
    # Implementation
    pass

@flow(name="run_my_agent_cycle")
async def run_my_agent_cycle():
    """Main agent cycle."""
    logger.info("=== Starting My Agent Cycle ===")
    await my_task()
    logger.info("=== My Agent Cycle Complete ===")
EOF

# 3. Create __init__.py
touch maia/src/maia/my_agent/__init__.py

# 4. Add unit tests
cat > maia/tests/test_my_agent.py << 'EOF'
import pytest
from maia.my_agent import run_my_agent_cycle

@pytest.mark.asyncio
async def test_my_agent_cycle():
    """Test my agent cycle."""
    # Implementation
    pass
EOF

# 5. Add integration tests
cat > alkyone/tests/components/maia/test_my_agent.py << 'EOF'
import pytest
from maia.my_agent import run_my_agent_cycle

@pytest.mark.integration
@pytest.mark.asyncio
async def test_my_agent_integration():
    """Test my agent end-to-end."""
    # Implementation
    pass
EOF
```

### Adding New DAO Method

```python
# atlas/src/atlas/adapters/maia.py

class MaiaDAO(DatabaseAdapter):
    async def my_new_method(self, param: str) -> List[Dict[str, Any]]:
        """
        Description of what this method does.
        
        Args:
            param: Description of parameter
        
        Returns:
            List of results
        """
        query = """
            SELECT * FROM my_table
            WHERE column = %s
        """
        return await self._fetch_all(query, (param,))
```

---

## Documentation

### Update Documentation

When adding features:

1. **Code docstrings** - Document all public functions
2. **Component README** - Update `{component}/README.md`
3. **Project docs** - Update `docs/` if architecture changes
4. **Examples** - Add usage examples if applicable

### Documentation Style

- Use clear, concise language
- Include code examples
- Explain "why" not just "what"
- Update outdated docs when you find them

---

## Release Process

### Versioning

We use [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes

### Creating a Release

```bash
# 1. Update version in pyproject.toml
# atlas/pyproject.toml
version = "0.3.0"

# 2. Tag release
git tag -a v0.3.0 -m "Release v0.3.0"
git push origin v0.3.0

# 3. GitHub Actions will build and publish
```

---

## Getting Help

- **Questions**: Open a GitHub Discussion
- **Bugs**: Open a GitHub Issue
- **Features**: Open a GitHub Issue with `enhancement` label
- **Security**: Email maintainer directly

---

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive environment.

### Standards

- Be respectful and professional
- Accept constructive criticism gracefully
- Focus on what is best for the community
- Show empathy towards others

---

## Summary

Contributing to Pleiades:

- ✅ Follow coding standards (Black, isort, type hints)
- ✅ Write tests (unit + integration)
- ✅ Maintain coverage (70%+)
- ✅ Update documentation
- ✅ Use conventional commits
- ✅ Get PR approval before merge

**Thank you for contributing!** 🙏
