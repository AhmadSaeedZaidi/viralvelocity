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

# Copy source so editable installs resolve
COPY atlas/ atlas/
COPY maia/ maia/

# Install project dependencies system-wide. poetry 2.x `install` always
# installs from the committed poetry.lock and errors if it is out of date with
# pyproject.toml, so CI is fully pinned to the lock (rebuild triggered by the
# pyproject.toml / poetry.lock change filter in ci.yml).
#
# NOTE: alkyone is intentionally NOT built into this image. It is excluded by
# .dockerignore (separate build context) and unhooked from the CI jobs below
# (see ci.yml + docs/agent-consolidation-proposal.md, P4). Its own image is
# built separately for the 24/7 VPS integration runs.
RUN cd atlas && poetry install --no-interaction && \
    cd ../maia && poetry install --no-interaction

# Purge yt-dlp cache
RUN rm -rf ~/.cache/yt-dlp || true