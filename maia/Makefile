.PHONY: help install lint test test-unit clean

help:
	@echo "Maia Development Commands:"
	@echo "  make install     - Install dependencies (editable mode)"
	@echo "  make lint        - Run linters (black, isort, mypy)"
	@echo "  make test        - Run unit tests"
	@echo "  make test-unit   - Run unit tests (alias for test)"
	@echo "  make clean       - Clean build artifacts"

install:
	poetry install --with dev

lint-local:
	poetry run black src/maia tests
	poetry run isort src/maia tests
	poetry run mypy src/maia --config-file pyproject.toml --no-error-summary

lint:
	poetry run black --check src/maia tests
	poetry run isort --check-only src/maia tests
	poetry run mypy src/maia --config-file pyproject.toml --no-error-summary

test:
	poetry run pytest tests -v

test-unit: test

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf dist build .pytest_cache .mypy_cache htmlcov
