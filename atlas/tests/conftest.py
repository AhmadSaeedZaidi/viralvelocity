"""Pytest configuration and fixtures for Atlas tests."""

import os

import pytest


@pytest.fixture(scope="session")
def test_env() -> dict[str, str]:
    """
    Provide reference test environment variables.

    Returns an environment dict for reuse in other test modules.
    """
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test"),
        "VAULT_PROVIDER": os.getenv("VAULT_PROVIDER", "huggingface"),
        "HF_DATASET_ID": os.getenv("HF_DATASET_ID", "test/dataset"),
        "HF_TOKEN": os.getenv("HF_TOKEN", "hf_test_token"),
        "YOUTUBE_API_KEY_POOL_JSON": os.getenv(
            "YOUTUBE_API_KEY_POOL_JSON", '["test_key_1", "test_key_2", "test_key_3"]'
        ),
        "COMPLIANCE_MODE": os.getenv("COMPLIANCE_MODE", "true"),
        "ENV": os.getenv("ENV", "test"),
        "JANITOR_ENABLED": os.getenv("JANITOR_ENABLED", "false"),
        "JANITOR_RETENTION_DAYS": os.getenv("JANITOR_RETENTION_DAYS", "7"),
    }

