# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps: make, git, ffmpeg, yt-dlp, Deno, build essentials
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        make git build-essential ffmpeg curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Deno
RUN curl -fsSL https://deno.land/install.sh | sh && \
    mv /root/.deno/bin/deno /usr/local/bin/deno

# Install Poetry
ENV POETRY_VERSION=2.0.0
# CRITICAL FIX: Turn off virtualenvs. Install dependencies globally in the container system Python.
RUN pip install --no-cache-dir poetry==${POETRY_VERSION} && \
    poetry config virtualenvs.create false

WORKDIR /workspace

# Copy dependency manifests first (cache layer)
COPY atlas/pyproject.toml atlas/poetry.lock* atlas/
COPY maia/pyproject.toml maia/poetry.lock* maia/
COPY pleiades/pyproject.toml pleiades/poetry.lock* pleiades/

# Copy source so editable installs resolve
COPY atlas/ atlas/
COPY maia/ maia/
COPY pleiades/ pleiades/

# Install all project dependencies system-wide
RUN cd atlas && poetry install --no-interaction && \
    cd ../maia && poetry install --no-interaction && \
    cd ../pleiades && poetry install --no-interaction

# Purge yt-dlp cache
RUN rm -rf ~/.cache/yt-dlp || true