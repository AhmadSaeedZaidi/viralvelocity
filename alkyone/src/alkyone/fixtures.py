import asyncio
import logging
import os
from typing import Any, AsyncGenerator, Dict, List

import pytest
import pytest_asyncio
from atlas.config import settings
from atlas.db import db

# --- ENVIRONMENT OVERRIDES ---
os.environ["ENV"] = "dev"
os.environ["COMPLIANCE_MODE"] = "False"

logger = logging.getLogger("alkyone.fixtures")

_uploaded_files: List[str] = []


@pytest_asyncio.fixture(scope="session")
async def system_init() -> AsyncGenerator[None, None]:
    """
    Session-level setup.
    Validates that HuggingFace credentials are configured for integration tests.
    Note: DB pool is now managed per-test by fresh_db fixture.
    """
    logger.info("Alkyone: Initializing System for Testing...")

    if not os.getenv("HF_TOKEN"):
        logger.warning(
            "HF_TOKEN not set! Integration tests requiring vault will fail. "
            "Set it with: export HF_TOKEN='hf_xxxxx'"
        )

    if not os.getenv("HF_DATASET_ID"):
        logger.warning(
            "HF_DATASET_ID not set! Using fallback or tests will fail. "
            "Set it with: export HF_DATASET_ID='username/pleiades-test-vault'"
        )

    yield
    await _cleanup_hf_uploads()
    logger.info("Alkyone: System Teardown Complete.")


@pytest_asyncio.fixture(scope="function")
async def fresh_db(system_init: Any) -> AsyncGenerator[None, None]:
    """
    Function-level fixture.
    Wipes and Re-Provisions the DB schema before EVERY test function.
    This ensures total test isolation.

    Manages connection pool lifecycle per-test to prevent pool exhaustion.
    """
    if db._pool is not None:
        await db.close()
        db._pool = None

    await db.initialize()

    try:
        async with db.get_connection() as conn:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            try:
                async with conn.transaction():
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
            except Exception as e:
                logger.warning(f"TimescaleDB extension skipped (safe to ignore if pre-loaded): {e}")

            import atlas

            schema_path = os.path.join(os.path.dirname(atlas.__file__), "schema.sql")

            if not os.path.exists(schema_path):
                raise FileNotFoundError(f"Could not find schema.sql at {schema_path}")

            with open(schema_path, "r") as f:
                sql_script = f.read()

                filtered_lines = []
                for line in sql_script.split("\n"):
                    if not line.strip().startswith("CREATE EXTENSION"):
                        filtered_lines.append(line)

                filtered_script = "\n".join(filtered_lines)
                await conn.execute(filtered_script)

        yield

    finally:
        await db.close()
        db._pool = None


def track_hf_upload(path: str) -> None:
    """Track a file uploaded to HuggingFace for cleanup."""
    global _uploaded_files
    if path not in _uploaded_files:
        _uploaded_files.append(path)
        logger.debug(f"Tracked HF upload: {path}")


async def _cleanup_hf_uploads() -> None:
    """Clean up all files uploaded to HuggingFace during tests."""
    global _uploaded_files

    if not _uploaded_files:
        logger.info("No HuggingFace uploads to clean up.")
        return

    logger.info(f"Cleaning up {len(_uploaded_files)} files from HuggingFace...")

    try:
        from huggingface_hub import HfApi

        token = os.getenv("HF_TOKEN")
        repo_id = os.getenv("HF_DATASET_ID")

        if not token or not repo_id:
            logger.warning("Cannot cleanup HF uploads: HF_TOKEN or HF_DATASET_ID not set")
            return

        api = HfApi(token=token)

        for file_path in _uploaded_files:
            try:
                api.delete_file(
                    path_in_repo=file_path,
                    repo_id=repo_id,
                    repo_type="dataset",
                    commit_message=f"Test cleanup: Remove {file_path}",
                )
                logger.debug(f"Deleted HF file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete {file_path}: {e}")

        logger.info("HuggingFace cleanup complete.")
        _uploaded_files.clear()

    except ImportError:
        logger.warning("huggingface_hub not installed, skipping cleanup")
    except Exception as e:
        logger.error(f"Error during HuggingFace cleanup: {e}")


# --- TEST DATA FIXTURES ---


@pytest.fixture
def mock_search_queue_item() -> Dict[str, Any]:
    """Mock search queue item for Hunter tests."""
    return {
        "id": 1,
        "query_term": "test query",
        "next_page_token": None,
        "last_searched_at": None,
        "priority": 5,
    }


@pytest.fixture
def mock_youtube_search_response() -> Dict[str, Any]:
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
def mock_tracker_target() -> Dict[str, Any]:
    """Mock tracker target video."""
    return {
        "id": "TEST123",
        "title": "Test Video",
        "published_at": "2026-01-15T10:00:00Z",
        "last_updated_at": None,
    }


@pytest.fixture
def mock_youtube_stats_response() -> Dict[str, Any]:
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
