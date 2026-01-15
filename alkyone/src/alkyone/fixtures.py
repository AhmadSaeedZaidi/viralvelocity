import asyncio
import logging
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from atlas.config import settings

# We import the infrastructure directly from Atlas
from atlas.db import db

# --- ENVIRONMENT OVERRIDES ---
# Crucial: Ensure we never accidentally nuke Production
os.environ["ENV"] = "test"
os.environ["COMPLIANCE_MODE"] = "False"

logger = logging.getLogger("alkyone.fixtures")


@pytest_asyncio.fixture(scope="session")
async def system_init():
    """
    Session-level setup.
    Initializes the DB connection pool once for the whole test suite.
    """
    logger.info("Alkyone: Initializing System for Testing...")
    await db.initialize()
    yield
    await db.close()
    logger.info("Alkyone: System Teardown Complete.")


@pytest_asyncio.fixture(scope="function")
async def fresh_db(system_init) -> AsyncGenerator:
    """
    Function-level fixture.
    Wipes and Re-Provisions the DB schema before EVERY test function.
    This ensures total test isolation.
    """
    async with db.get_connection() as conn:
        # 1. Nuke the world (DROP SCHEMA public)
        # We use CASCADE to kill all tables, views, and extensions in one go.
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

        # 2. Re-enable extensions (pgvector gets dropped with schema)
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        # 3. Provision Schema using Atlas's own SQL definition
        # We look up the file location dynamically from the installed package
        import atlas.schema

        schema_path = os.path.join(os.path.dirname(atlas.schema.__file__), "schema.sql")

        if not os.path.exists(schema_path):
            raise FileNotFoundError(f"Could not find schema.sql at {schema_path}")

        with open(schema_path, "r") as f:
            sql_script = f.read()
            await conn.execute(sql_script)

    yield
    # No teardown needed; the next test will nuke it anyway.


# --- TEST DATA FIXTURES ---


@pytest.fixture
def mock_search_queue_item():
    """Mock search queue item for Hunter tests."""
    return {
        "id": 1,
        "query_term": "test query",
        "next_page_token": None,
        "last_searched_at": None,
        "priority": 5,
    }


@pytest.fixture
def mock_youtube_search_response():
    """Mock YouTube search API response."""
    return {
        "items": [
            {
                "id": {"videoId": "TEST123"},
                "snippet": {
                    "title": "Test Video",
                    "channelId": "CHANNEL123",
                    "channelTitle": "Test Channel",
                    "publishedAt": "2026-01-15T10:00:00Z",
                    "description": "Test description",
                    "tags": ["test", "video"],
                },
            }
        ],
        "nextPageToken": "NEXT_TOKEN",
    }


@pytest.fixture
def mock_tracker_target():
    """Mock tracker target video."""
    return {
        "id": "TEST123",
        "title": "Test Video",
        "published_at": "2026-01-15T10:00:00Z",
        "last_updated_at": None,
    }


@pytest.fixture
def mock_youtube_stats_response():
    """Mock YouTube statistics API response."""
    return {
        "items": [
            {
                "id": "TEST123",
                "snippet": {"publishedAt": "2026-01-15T10:00:00Z"},
                "statistics": {
                    "viewCount": "1000",
                    "likeCount": "100",
                    "commentCount": "10",
                },
            }
        ]
    }
