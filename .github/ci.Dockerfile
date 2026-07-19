# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps: make, git, ffmpeg, yt-dlp, Deno, build essentials
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        make git build-essential ffmpeg curl unzip ca-certificates nodejs npm jq && \
    rm -rf /var/lib/apt/lists/*

# Install Deno
RUN curl -fsSL https://deno.land/install.sh | sh && \
    mv /root/.deno/bin/deno /usr/local/bin/deno

# Install Poetry
ENV POETRY_VERSION=2.0.0
# Install dependencies globally in the container system Python.
RUN pip install --no-cache-dir poetry==${POETRY_VERSION} && \
    poetry config virtualenvs.create false

WORKDIR /workspace

# Copy dependency manifests first (cache layer)
COPY atlas/pyproject.toml atlas/poetry.lock* atlas/
COPY maia/pyproject.toml maia/poetry.lock* maia/
COPY alkyone/pyproject.toml alkyone/poetry.lock* alkyone/

# Copy source so editable installs resolve
COPY atlas/ atlas/
COPY maia/ maia/
COPY alkyone/ alkyone/
COPY mcp/ mcp/

# Install project dependencies system-wide. poetry 2.x `install` always
# installs from the committed poetry.lock and errors if it is out of date with
# pyproject.toml, so CI is fully pinned to the lock (rebuild triggered by the
# pyproject.toml / poetry.lock change filter in ci.yml).
#
# alkyone is now part of the image so `make -C alkyone lint` runs in per-PR CI.
# Its integration tests stay manual/on-demand (see .github/workflows/alkyone.yml)
# and are guarded against production by alkyone/src/alkyone/guard.py.
RUN cd atlas && poetry install --no-interaction && \
    cd ../maia && poetry install --no-interaction && \
    cd ../alkyone && poetry install --no-interaction

# The mcp package is a plain (non-poetry) project. Install its third-party
# runtime + dev deps system-wide so `make -C mcp lint` and `make -C mcp test`
# run in per-PR CI. Its own source is imported via pytest pythonpath (see
# mcp/pyproject.toml), so a lightweight deps install is sufficient.
RUN pip install --no-cache-dir \
        "mcp>=1.2.0,<2.0.0" \
        "openai>=1.30.0,<2.0.0" \
        "ruff>=0.15.0,<1.0.0" \
        "pytest>=8.0.0,<9.0.0" \
        "pytest-asyncio>=0.23.0,<1.0.0"

# Purge yt-dlp cache
RUN rm -rf ~/.cache/yt-dlp || true