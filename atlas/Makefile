.PHONY: help install lint test test-unit migrate clean

help:
	@echo "Atlas Development Commands:"
	@echo "  make install     - Install dependencies (editable mode)"
	@echo "  make setup       - Provision database schema"
	@echo "  make lint        - Run linters (black, isort, mypy)"
	@echo "  make test        - Run unit tests"
	@echo "  make test-unit   - Run unit tests (alias for test)"
	@echo "  make migrate     - Apply database schema (requires DATABASE_URL)"
	@echo "  make clean       - Clean build artifacts"

install:
	poetry install --with dev --all-extras

setup:
	poetry run python -m atlas.setup

lint-local:
	poetry run black src/atlas tests
	poetry run isort src/atlas tests
	poetry run mypy src/atlas --config-file pyproject.toml --no-error-summary

lint:
	poetry run black --check src/atlas tests
	poetry run isort --check-only src/atlas tests
	poetry run mypy src/atlas --config-file pyproject.toml --no-error-summary

test:
	poetry run pytest tests -v

test-unit: test

migrate:
	@if [ -z "$$DATABASE_URL" ]; then \
		echo "ERROR: DATABASE_URL environment variable is required"; \
		exit 1; \
	fi
	psql "$$DATABASE_URL" -f src/atlas/schema.sql

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf dist build .pytest_cache .mypy_cache htmlcov


