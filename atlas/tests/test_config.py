"""Tests for configuration module."""
import json

import pytest
from pydantic import ValidationError


def test_settings_load(test_env):
    """Test settings load from environment."""
    from atlas import settings
    
    assert settings.ENV == "test"
    assert settings.COMPLIANCE_MODE is True
    assert settings.VAULT_PROVIDER == "huggingface"


def test_api_keys_compliance_mode(test_env):
    """Test API key pool respects compliance mode."""
    from atlas import settings
    
    keys = settings.api_keys
    assert len(keys) == 1
    assert keys[0] == "test_key"


def test_api_keys_json_parsing(test_env, monkeypatch):
    """Test API key pool JSON parsing."""
    monkeypatch.setenv("YOUTUBE_API_KEY_POOL_JSON", '["key1", "key2", "key3"]')
    monkeypatch.setenv("COMPLIANCE_MODE", "false")
    
    from atlas.config import Settings
    settings = Settings()
    
    keys = settings.api_keys
    assert len(keys) == 3


def test_vault_validation_hf(test_env, monkeypatch):
    """Test HuggingFace vault requires proper config."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    
    from atlas.config import Settings
    
    with pytest.raises(ValidationError):
        Settings()


def test_vault_validation_gcs(test_env, monkeypatch):
    """Test GCS vault requires proper config."""
    monkeypatch.setenv("VAULT_PROVIDER", "gcs")
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    
    from atlas.config import Settings
    
    with pytest.raises(ValidationError):
        Settings()


