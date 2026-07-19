.PHONY: help install lint lint-local lint-fix test clean

PYTHON ?= python
PIP ?= pip

help:
	@echo "Pleiades MCP package commands:"
	@echo "  make install     - Install the mcp package (editable) + mcp SDK"
	@echo "  make lint        - Run ruff (readonly) on the package"
	@echo "  make lint-fix    - Auto-fix lint issues"
	@echo "  make test        - Run unit tests"
	@echo "  make clean       - Remove build artifacts"

install:
	$(PIP) install -e .

lint-local:
	$(PYTHON) -m ruff check src tests

lint:
	$(PYTHON) -m ruff check src tests

lint-fix:
	$(PYTHON) -m ruff check --fix src tests
	$(PYTHON) -m ruff format src tests

test:
	$(PYTHON) -m pytest tests/ -q

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
