# maia/Dockerfile
# Phase 4: The Hydra Protocol
# Builds a lightweight image containing Atlas (lib) and Maia (agent).
# 
# Build Context: Project root (pleiades/)
# Build: docker build -f maia/Dockerfile -t pleiades-maia:latest .
# Run Hunter: docker run --env-file .env pleiades-maia:latest python -m maia hunter
# Run Tracker: docker run --env-file .env pleiades-maia:latest python -m maia tracker

FROM python:3.11-slim

# Prevent Python from writing pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
# - libpq-dev: Required for psycopg3 (PostgreSQL adapter)
# - gcc: Required for building Python extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Atlas (The Brain)
# Atlas must be installed first as Maia depends on it
COPY atlas /app/atlas
RUN pip install --no-cache-dir -e /app/atlas

# Copy and install Maia (The Agent)
COPY maia /app/maia
RUN pip install --no-cache-dir -e /app/maia

# Set Python path to include source directories
ENV PYTHONPATH="${PYTHONPATH}:/app/atlas/src:/app/maia/src"

# Health check - verify imports work
RUN python -c "from maia import __version__; print(f'Maia v{__version__} ready')"

# Default command: Run Hunter
# Override with: docker run ... python -m maia tracker
CMD ["python", "-m", "maia", "hunter"]