# Contributing to Atlas

Thank you for your interest in contributing to Atlas, the infrastructure kernel for the Pleiades platform!

## Development Setup

### Prerequisites
- Python 3.11 or 3.12
- Poetry (package manager)
- PostgreSQL with pgvector extension (for testing)

### Installation

```bash
# Clone the repository
cd pleiades/atlas

# Install dependencies
make install
# or: poetry install --with dev --all-extras

# Copy environment template
cp ENV.example .env
# Edit .env with your credentials
```

### Database Setup

```bash
# Provision the schema
make setup
# or: python -m atlas.setup
```

## Development Workflow

### 1. Code Style

We use Black and isort for consistent formatting:

```bash
# Check formatting
make lint

# Auto-format
make format
```

**Style Guidelines**:
- Line length: 100 characters
- Use type hints for all function signatures
- Prefer explicit over implicit
- Minimize comments (code should be self-documenting)
- Use docstrings for public APIs only

### 2. Type Checking

We enforce strict type checking with mypy:

```bash
make type-check
```

**Type Checking Rules**:
- All functions must have parameter and return type hints
- Use `Optional[T]` for nullable values
- Use `Dict[K, V]` not `dict` in type hints (Python 3.11)
- Avoid `Any` unless interfacing with untyped libraries

### 3. Testing

```bash
# Run unit tests
make test

# Run smoke tests (verify live service connectivity)
make smoke-test

# Run specific test file
poetry run pytest tests/test_config.py

# Run with coverage
poetry run pytest --cov=atlas --cov-report=html
```

**Testing Guidelines**:
- Write unit tests for all new functionality
- Use fixtures from `conftest.py`
- Mock external services (don't hit real APIs in unit tests)
- Mark integration tests with `@pytest.mark.integration`
- Mark slow tests with `@pytest.mark.slow`
- Smoke tests verify actual connectivity (database, vault, API keys)

### 4. Writing Tests

**Unit Test Example**:
```python
def test_validate_youtube_id():
    """Test YouTube ID validation."""
    assert validate_youtube_id("dQw4w9WgXcQ") is True
    assert validate_youtube_id("invalid") is False
```

**Async Test Example**:
```python
@pytest.mark.asyncio
async def test_event_emission(db_connection):
    """Test event bus emission."""
    await events.emit("test.event", "entity123", {"key": "value"})
    # Verify in database
```

**Using Fixtures**:
```python
def test_storage(mock_vault):
    """Test vault operations."""
    mock_vault.store_json("test.json", {"data": "test"})
    result = mock_vault.fetch_json("test.json")
    assert result["data"] == "test"
```

## Code Organization

### Module Structure

```
atlas/
├── __init__.py        # Public API exports
├── config.py          # Settings and validation
├── db.py              # Database connection pool
├── vault.py           # Storage strategy pattern
├── events.py          # Event bus
├── notifications.py   # Alert system
├── utils.py           # Helper functions
├── setup.py           # Schema provisioning
└── schema.sql         # Database schema
```

### Adding a New Module

1. Create the module file in `src/atlas/`
2. Add comprehensive type hints
3. Write unit tests in `tests/test_<module>.py`
4. Export public APIs in `__init__.py`
5. Update `__all__` list
6. Document in README.md

## Pull Request Process

### 1. Branch Naming
- Feature: `feature/description`
- Bugfix: `fix/description`
- Refactor: `refactor/description`

### 2. Commit Messages
Follow conventional commits:
```
feat: add retry decorator to utils
fix: correct timezone handling in events
docs: update architecture documentation
refactor: improve vault error handling
test: add coverage for config validation
```

### 3. Before Submitting

```bash
# Ensure all checks pass
make format
make lint
make type-check
make test
```

### 4. PR Description Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] All tests passing

## Checklist
- [ ] Code follows style guidelines
- [ ] Type hints added
- [ ] Documentation updated
- [ ] Changelog updated
```

## Architecture Guidelines

### Singleton Pattern
Use for resource-intensive services:
```python
class ServiceManager:
    _instance: Optional["ServiceManager"] = None

    def __new__(cls) -> "ServiceManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
```

### Strategy Pattern
Use for swappable implementations:
```python
class Strategy(abc.ABC):
    @abc.abstractmethod
    def execute(self) -> None:
        pass

class ConcreteStrategy(Strategy):
    def execute(self) -> None:
        # Implementation
        pass
```

### Async Context Managers
For resource cleanup:
```python
@asynccontextmanager
async def managed_resource() -> AsyncGenerator[Resource, None]:
    resource = await acquire()
    try:
        yield resource
    finally:
        await release(resource)
```

## Error Handling

### When to Raise
- Critical path failures (database connection)
- Invalid configuration
- Data corruption

### When to Log and Continue
- Observability operations (events, notifications)
- Optional features
- Fallback scenarios

### Error Messages
```python
# Good
raise ValueError(f"Invalid video ID format: {video_id}")

# Bad
raise ValueError("Invalid ID")
```

## Performance Considerations

### Async Operations
- Use `async with` for connections
- Avoid blocking operations in async context
- Use `asyncio.gather()` for parallel operations

### Database Queries
- Use connection pool (never create connections directly)
- Parameterize queries (prevent SQL injection)
- Add indices for frequent queries

### Storage Operations
- Batch uploads when possible
- Use appropriate compression
- Consider rate limits

## Documentation

### Docstrings
Only for public APIs:
```python
def validate_youtube_id(video_id: str) -> bool:
    """
    Validate YouTube video ID format.
    
    Args:
        video_id: YouTube video ID to validate
        
    Returns:
        True if valid, False otherwise
    """
```

### Type Hints as Documentation
Let types speak:
```python
# Good
def process_video(
    video_id: str,
    metadata: Dict[str, Any],
    retry: bool = False
) -> Optional[ProcessingResult]:
    pass

# Bad (needs docstring to understand)
def process(id, data, flag=False):
    pass
```

## Release Process

1. Update `CHANGELOG.md`
2. Bump version in `pyproject.toml` and `__init__.py`
3. Run full test suite
4. Create git tag: `v0.X.X`
5. Build: `poetry build`
6. Publish: `poetry publish` (maintainers only)

## Getting Help

- Architecture questions: See [architecture.md](architecture.md)
- Usage questions: See [quickstart.md](quickstart.md)
- Bug reports: Open an issue
- Feature requests: Open an issue with proposal

## Code of Conduct

- Be respectful and constructive
- Focus on the code, not the person
- Welcome newcomers
- Share knowledge

Thank you for contributing to Atlas! 🚀


