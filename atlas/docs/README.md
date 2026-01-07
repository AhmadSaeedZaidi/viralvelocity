# Atlas Documentation

Complete documentation for the Pleiades infrastructure kernel.

## Getting Started

- **[Quick Start Guide](quickstart.md)** - Get Atlas running in 5 minutes
  - Installation steps
  - Configuration setup
  - First smoke test
  - Basic usage examples

## Reference

- **[API Reference](api-reference.md)** - Complete API documentation
  - Module interfaces
  - Function signatures
  - Database schema
  - Type definitions
  - Error handling

## Architecture

- **[Architecture Guide](architecture.md)** - System design and patterns
  - Design principles
  - Component architecture
  - Data flow
  - Performance characteristics
  - Extension points

## Development

- **[Contributing Guide](contributing.md)** - Development workflow
  - Setup instructions
  - Code style guidelines
  - Testing strategy
  - PR process
  - Architecture patterns

- **[Migration Guide](migration.md)** - Upgrading to v0.2.0
  - Breaking changes (none)
  - New features
  - Recommended updates
  - Configuration changes

## Additional Resources

- **[Summary](summary.md)** - Complete v0.2.0 release overview
- **[Changelog](../CHANGELOG.md)** - Version history and release notes
- **[Examples](../examples/)** - Code examples and usage patterns
- **[Environment Template](../ENV.example)** - Configuration template

## Quick Links

### Common Tasks

**Initial Setup:**
```bash
cd atlas
poetry install --extras all --with dev
cp ENV.example .env
make setup
make smoke-test
```

**Development Workflow:**
```bash
make format      # Format code
make lint        # Check style
make type-check  # Run mypy
make test        # Run tests
```

**Usage:**
```python
from atlas import db, vault, events, notifier

# See quickstart.md for complete examples
```

### Documentation Structure

```
docs/
├── README.md          # This file - documentation index
├── quickstart.md      # Getting started guide
├── api-reference.md   # Complete API documentation
├── architecture.md    # System design and patterns
├── contributing.md    # Development guidelines
└── migration.md       # Upgrade guide
```

## Support

- **Issues**: Report bugs or request features on GitHub
- **Questions**: See the guides above or check examples
- **Contributing**: Read the [Contributing Guide](contributing.md)

## Version

Current version: **0.2.0**

See [Changelog](../CHANGELOG.md) for release history.

