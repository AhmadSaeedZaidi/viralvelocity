.PHONY: help install lint test clean

help:
	@echo "Alkyone Development Commands:"
	@echo "  make install     - Install dependencies (editable mode)"
	@echo "  make lint        - Run linters (black, isort, mypy)"
	@echo "  make test        - Run unit tests"
	@echo "  make clean       - Clean build artifacts"

install:
	poetry install --with dev

lint:
	poetry run black --check src/alkyone tests
	poetry run isort --check-only src/alkyone tests
	poetry run mypy src/alkyone --config-file pyproject.toml --no-error-summary

test:
	poetry run pytest -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf dist build .pytest_cache .mypy_cache htmlcov
