"""Pytest configuration and fixtures for Atlas tests."""
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def test_env() -> None:
    """Set up test environment variables."""
    os.environ.update({
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "VAULT_PROVIDER": "huggingface",
        "HF_DATASET_ID": "test/dataset",
        "HF_TOKEN": "hf_test_token",
        "YOUTUBE_API_KEY_POOL_JSON": '["test_key"]',
        "COMPLIANCE_MODE": "true",
        "ENV": "test",
    })


@pytest_asyncio.fixture
async def db_connection(test_env):
    """Provide a test database connection."""
    from atlas import db
    
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
def mock_vault(monkeypatch):
    """Mock vault for testing without actual storage."""
    storage = {}
    
    def mock_store(path: str, data):
        storage[path] = data
    
    def mock_fetch(path: str):
        return storage.get(path)
    
    def mock_list(prefix: str):
        return [k for k in storage.keys() if k.startswith(prefix)]
    
    from atlas import vault
    monkeypatch.setattr(vault, "store_json", mock_store)
    monkeypatch.setattr(vault, "fetch_json", mock_fetch)
    monkeypatch.setattr(vault, "list_files", mock_list)
    
    return vault


