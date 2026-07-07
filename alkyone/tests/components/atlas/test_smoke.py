"""
Smoke tests for verifying live service connectivity.
Run with: pytest tests/test_smoke.py or make smoke-test
"""

import pytest
from atlas.vault import get_vault

from atlas import db, settings


@pytest.fixture(autouse=True)
async def reset_db_singleton():
    """Force reset the DB singleton to prevent event loop mismatch."""
    # If a pool exists from a previous loop, detach it (loop is dead)
    if db._pool:
        db._pool = None
    yield
    # Ensure clean closure after test
    await db.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_connectivity(reset_db_singleton):
    """Verify actual database connection to Neon."""
    # Initialize fresh connection on the CURRENT loop
    await db.initialize()

    is_healthy = await db.health_check()
    assert is_healthy, "Database health check failed - verify DATABASE_URL in .env"
    # db.close() is handled by the fixture


@pytest.mark.integration
def test_vault_configuration():
    """
    Verify vault provider is properly configured with REAL credentials.

    This test ensures that HF_TOKEN and HF_DATASET_ID are real values,
    not mock placeholders.
    """
    import os

    provider = settings.VAULT_PROVIDER

    if provider == "huggingface":
        # Validate environment variables
        hf_token = os.getenv("HF_TOKEN")
        hf_dataset_id = os.getenv("HF_DATASET_ID")

        assert hf_token is not None, (
            "HF_TOKEN not set! Set it with: export HF_TOKEN='hf_xxxxxxxxxxxxx'"
        )
        assert hf_token != "mock_token", "HF_TOKEN is 'mock_token'! Use a real token."

        assert hf_dataset_id is not None, (
            "HF_DATASET_ID not set! "
            "Set it with: export HF_DATASET_ID='username/pleiades-test-vault'"
        )
        assert hf_dataset_id != "mock/dataset", (
            "HF_DATASET_ID is 'mock/dataset'! Use a real dataset."
        )

        # Validate settings
        assert settings.HF_DATASET_ID is not None, "HF_DATASET_ID not configured in settings"
        assert settings.HF_TOKEN is not None, "HF_TOKEN not configured in settings"

    elif provider == "gcs":
        assert settings.GCS_BUCKET_NAME is not None, "GCS_BUCKET_NAME not configured"

    assert get_vault() is not None, "Vault instance not initialized"


@pytest.mark.integration
def test_api_keys_loaded():
    """Verify YouTube API keys are loaded."""
    keys = settings.api_keys
    assert len(keys) > 0, "No API keys loaded from YOUTUBE_API_KEY_POOL_JSON"
    assert all(len(key) > 10 for key in keys), "Invalid API key format"


@pytest.mark.integration
def test_configuration_complete():
    """Verify all critical configuration is present."""
    assert settings.DATABASE_URL is not None, "DATABASE_URL not set"
    assert settings.ENV in ["dev", "prod", "test"], "ENV not properly configured"
    assert isinstance(settings.COMPLIANCE_MODE, bool), "COMPLIANCE_MODE must be boolean"
