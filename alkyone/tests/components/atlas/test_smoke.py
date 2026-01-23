"""
Smoke tests for verifying live service connectivity.
Run with: pytest tests/test_smoke.py or make smoke-test
"""

import pytest
from atlas import db, settings, vault


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_connectivity():
    """Verify actual database connection to Neon."""
    is_healthy = await db.health_check()
    assert is_healthy, "Database health check failed - verify DATABASE_URL in .env"
    await db.close()


@pytest.mark.integration
def test_vault_configuration():
    """Verify vault provider is properly configured."""
    provider = settings.VAULT_PROVIDER

    if provider == "huggingface":
        assert settings.HF_DATASET_ID is not None, "HF_DATASET_ID not configured"
        assert settings.HF_TOKEN is not None, "HF_TOKEN not configured"
    elif provider == "gcs":
        assert settings.GCS_BUCKET_NAME is not None, "GCS_BUCKET_NAME not configured"

    assert vault is not None, "Vault instance not initialized"


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
