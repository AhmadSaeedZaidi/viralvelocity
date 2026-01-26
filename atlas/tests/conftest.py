"""Pytest configuration and fixtures for Atlas tests."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def test_env() -> Dict[str, str]:
    """
    Provide test environment variables for reference.
    
    Returns environment dict for reuse in other test modules.
    """
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test"),
        "VAULT_PROVIDER": os.getenv("VAULT_PROVIDER", "huggingface"),
        "HF_DATASET_ID": os.getenv("HF_DATASET_ID", "test/dataset"),
        "HF_TOKEN": os.getenv("HF_TOKEN", "hf_test_token"),
        "YOUTUBE_API_KEY_POOL_JSON": os.getenv("YOUTUBE_API_KEY_POOL_JSON", '["test_key_1", "test_key_2", "test_key_3"]'),
        "COMPLIANCE_MODE": os.getenv("COMPLIANCE_MODE", "true"),
        "ENV": os.getenv("ENV", "test"),
        "JANITOR_ENABLED": os.getenv("JANITOR_ENABLED", "false"),
        "JANITOR_RETENTION_DAYS": os.getenv("JANITOR_RETENTION_DAYS", "7"),
    }


# @pytest_asyncio.fixture
# async def db_connection(test_env):
#     """
#     Provide a test database connection with proper lifecycle management.

#     Note: Uses Atlas's db module for consistent connection handling.
#     """
#     from atlas import db

#     # Initialize connection pool
#     await db.initialize()

#     # Provide connection to tests
#     yield db

#     # Cleanup
#     await db.close()


@pytest.fixture
def mock_vault(monkeypatch):
    """
    Mock vault for testing without actual storage.

    Provides in-memory storage for testing vault operations
    without requiring real HuggingFace or GCS credentials.
    """
    storage: Dict[str, Any] = {}

    def mock_store(path: str, data: Any) -> None:
        storage[path] = data

    def mock_fetch(path: str) -> Any:
        return storage.get(path)

    def mock_list(prefix: str) -> list[str]:
        return [k for k in storage.keys() if k.startswith(prefix)]

    def mock_append_metrics(data: list[dict], date: str = None, hour: str = None) -> None:
        """Mock metrics append for testing."""
        key = f"metrics/{date or 'test'}/{hour or '00'}/stats.parquet"
        storage[key] = data

    from atlas import vault

    monkeypatch.setattr(vault.vault, "store_json", mock_store)
    monkeypatch.setattr(vault.vault, "fetch_json", mock_fetch)
    monkeypatch.setattr(vault.vault, "list_files", mock_list)
    monkeypatch.setattr(vault.vault, "append_metrics", mock_append_metrics)

    # Return vault instance for inspection
    return vault.vault


@pytest.fixture
def mock_key_ring(monkeypatch):
    """
    Mock KeyRing for testing without real API keys.

    Provides deterministic key rotation for testing.
    """
    from atlas.utils import KeyRing

    class MockKeyRing:
        def __init__(self, pool_name: str):
            self.pool_name = pool_name
            self.keys = ["test_key_1", "test_key_2", "test_key_3"]
            self._session_attempts: Dict[int, int] = {}

        def start_session(self, session_id: int = None) -> int:
            if session_id is None:
                session_id = id(self)
            self._session_attempts[session_id] = 0
            return session_id

        def get_session_key(self, session_id: int) -> str:
            attempt = self._session_attempts.get(session_id, 0)
            return self.keys[attempt % len(self.keys)]

        def attempt_rotation(self, session_id: int) -> bool:
            self._session_attempts[session_id] = self._session_attempts.get(session_id, 0) + 1
            return self._session_attempts[session_id] < len(self.keys)

        def end_session(self, session_id: int) -> None:
            self._session_attempts.pop(session_id, None)

        @property
        def size(self) -> int:
            return len(self.keys)

    monkeypatch.setattr("atlas.utils.KeyRing", MockKeyRing)
    return MockKeyRing
