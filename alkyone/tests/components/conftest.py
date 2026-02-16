"""
Global pytest configuration for Alkyone.
Imports fixtures from src/alkyone/fixtures.py to make them available to all tests.

CRITICAL: This conftest does NOT mock Vault or DAO for integration tests.
Integration tests use REAL infrastructure (HuggingFace, Neon DB, YouTube API).

Environment variables are provided by:
- CI: GitHub Secrets and Variables (see .github/workflows/ci.yml)
- Local: Shell environment (export VAR=value)
- Atlas: Pydantic BaseSettings (automatically loads from environment)

DO NOT use dotenv here - Atlas handles environment loading.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest


def pytest_configure(config):
    """
    Register custom markers for test categorization.

    This runs once at the start of the test session.
    """
    config.addinivalue_line(
        "markers", "integration: marks tests requiring REAL infrastructure (DB, Vault, API)"
    )


def pytest_collection_modifyitems(config, items):
    """
    SAFETY INTERLOCK: Skip integration tests if real infrastructure is unavailable.

    This prevents integration tests from running with mocked/dummy data, which would
    give false confidence. Integration tests either run against REAL services or don't run at all.

    Required for integration tests:
    - DATABASE_URL: Real Neon PostgreSQL connection
    - HF_TOKEN + HF_DATASET_ID: Real HuggingFace vault
    - YOUTUBE_API_KEY_POOL_JSON: Real YouTube API keys (not dummy/test keys)

    If ANY credential is missing, integration tests are SKIPPED (not failed, not mocked).
    """
    # Check environment for real credentials
    has_real_db = bool(os.getenv("DATABASE_URL"))
    has_real_vault = bool(os.getenv("HF_TOKEN")) and bool(os.getenv("HF_DATASET_ID"))
    api_keys_json = os.getenv("YOUTUBE_API_KEY_POOL_JSON", "")
    has_real_api = bool(api_keys_json) and not any(
        marker in api_keys_json.upper() for marker in ["DUMMY", "TEST", "FAKE", "MOCK"]
    )

    # Prepare skip marker with detailed message
    missing = []
    if not has_real_db:
        missing.append("DATABASE_URL")
    if not has_real_vault:
        missing.append("HF_TOKEN/HF_DATASET_ID")
    if not has_real_api:
        missing.append("YOUTUBE_API_KEY_POOL_JSON (real keys)")

    if missing:
        skip_msg = (
            f"Integration tests require REAL infrastructure. Missing: {', '.join(missing)}. "
            f"In CI: Check GitHub Secrets. Local: Set environment variables."
        )
        skip_integration = pytest.mark.skip(reason=skip_msg)

        # Apply skip marker to all integration tests
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)


@pytest.fixture
def mock_sleep():
    """Mock asyncio.sleep to speed up unit tests."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock


# Import fixtures from alkyone.fixtures
from alkyone.fixtures import (
    fresh_db,
    mock_search_queue_item,
    mock_tracker_target,
    mock_youtube_search_response,
    mock_youtube_stats_response,
    system_init,
)
