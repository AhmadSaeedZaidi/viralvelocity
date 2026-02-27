# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps: ffmpeg, yt-dlp, Deno, build tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Deno
RUN curl -fsSL https://deno.land/install.sh | sh && \
    mv /root/.deno/bin/deno /usr/local/bin/deno

# Install Poetry
ENV POETRY_VERSION=2.0.0
RUN pip install --no-cache-dir poetry==${POETRY_VERSION} && \
    poetry config virtualenvs.in-project true

WORKDIR /workspace

# Copy dependency manifests first (cache layer)
COPY atlas/pyproject.toml atlas/poetry.lock* atlas/
COPY maia/pyproject.toml maia/poetry.lock* maia/
COPY alkyone/pyproject.toml alkyone/poetry.lock* alkyone/

# Copy source so editable installs resolve
COPY atlas/ atlas/
COPY maia/ maia/
COPY alkyone/ alkyone/

# Install all project dependencies
RUN cd atlas && poetry install --no-interaction && \
    cd ../maia && poetry install --no-interaction && \
    cd ../alkyone && poetry install --no-interaction

# Purge yt-dlp cache
RUN rm -rf ~/.cache/yt-dlp || true
