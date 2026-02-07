.PHONY: help install lint test test-int clean

help:
	@echo "Alkyone Development Commands:"
	@echo "  make install     - Install dependencies (editable mode)"
	@echo "  make lint        - Run linters (black, isort, mypy)"
	@echo "  make test        - Run integration tests"
	@echo "  make test-int    - Run integration tests (alias for test)"
	@echo "  make clean       - Clean build artifacts"

install:
	poetry install --with dev

lint-local:
	poetry run black src/alkyone tests
	poetry run isort src/alkyone tests
	poetry run mypy src/alkyone --config-file pyproject.toml --no-error-summary

lint:
	poetry run black --check src/alkyone tests
	poetry run isort --check-only src/alkyone tests
	poetry run mypy src/alkyone --config-file pyproject.toml --no-error-summary

test:
	poetry run pytest tests -v --tb=short

test-int: test

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf dist build .pytest_cache .mypy_cache htmlcov
