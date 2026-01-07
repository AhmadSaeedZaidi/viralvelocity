.PHONY: help install setup test smoke-test lint format type-check clean

help:
	@echo "Atlas Development Commands:"
	@echo "  make install      - Install dependencies (editable mode)"
	@echo "  make setup        - Provision database schema"
	@echo "  make test         - Run unit tests"
	@echo "  make smoke-test   - Verify live service connectivity"
	@echo "  make lint         - Run linters"
	@echo "  make format       - Format code"
	@echo "  make type-check   - Run type checker"
	@echo "  make clean        - Clean build artifacts"

install:
	@echo "Installing Atlas in editable mode with all extras..."
	poetry install --with dev --all-extras
	@echo "Installation complete (editable mode enabled)"

setup:
	@echo "Provisioning database schema..."
	poetry run python -m atlas.setup
	@echo "Schema provisioned"

test:
	@echo "Running unit tests..."
	poetry run pytest -v

smoke-test:
	@echo "Running smoke tests (verifying live service connectivity)..."
	poetry run pytest tests/test_smoke.py -v -m integration
	@echo "All smoke tests passed"

lint:
	poetry run black --check src/atlas tests
	poetry run isort --check-only src/atlas tests

format:
	poetry run black src/atlas tests examples
	poetry run isort src/atlas tests examples

type-check:
	poetry run mypy src/atlas

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf dist build .pytest_cache .mypy_cache htmlcov


