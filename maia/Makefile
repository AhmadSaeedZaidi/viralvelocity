.PHONY: help install install-dev clean test format lint type-check run-hunter run-tracker docker-build docker-run-hunter docker-run-tracker

help:
	@echo "Maia - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Install Maia in editable mode"
	@echo "  make install-dev    Install with development dependencies"
	@echo ""
	@echo "Development:"
	@echo "  make format         Format code with black and isort"
	@echo "  make lint           Run linting checks"
	@echo "  make type-check     Run type checking with mypy"
	@echo "  make test           Run pytest tests"
	@echo ""
	@echo "Execution:"
	@echo "  make run-hunter     Run Hunter cycle"
	@echo "  make run-tracker    Run Tracker cycle"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build         Build Maia Docker image"
	@echo "  make docker-run-hunter    Run Hunter in Docker"
	@echo "  make docker-run-tracker   Run Tracker in Docker"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean          Remove build artifacts and cache"

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

format:
	@echo "Formatting code with black..."
	black src/
	@echo "Sorting imports with isort..."
	isort src/

lint:
	@echo "Running black check..."
	black --check src/
	@echo "Running isort check..."
	isort --check-only src/

type-check:
	@echo "Running mypy type checking..."
	mypy src/maia

test:
	@echo "Running pytest..."
	pytest tests/ -v

run-hunter:
	@echo "Running Hunter cycle..."
	maia-hunter

run-tracker:
	@echo "Running Tracker cycle..."
	maia-tracker

docker-build:
	@echo "Building Maia Docker image..."
	docker build -f Dockerfile -t pleiades-maia:latest ..

docker-run-hunter:
	@echo "Running Hunter in Docker..."
	docker run --rm --env-file ../.env pleiades-maia:latest python -m maia hunter

docker-run-tracker:
	@echo "Running Tracker in Docker..."
	docker run --rm --env-file ../.env pleiades-maia:latest python -m maia tracker

