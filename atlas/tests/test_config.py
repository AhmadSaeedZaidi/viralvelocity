"""Tests for configuration module."""

import json

import pytest
from pydantic import ValidationError


def test_settings_load(test_env):
    """Test settings load from environment."""
    from atlas import settings

    assert settings.ENV == "dev"
    assert settings.COMPLIANCE_MODE is True
    assert settings.VAULT_PROVIDER == "huggingface"


def test_api_keys_compliance_mode(test_env):
    """Test API key pool respects compliance mode."""
    from atlas import settings

    keys = settings.api_keys
    assert len(keys) == 1
    assert keys[0] == "test_key_1"  # First key from fixture's key pool


def test_api_keys_json_parsing(test_env, monkeypatch):
    """Test API key pool JSON parsing."""
    monkeypatch.setenv("YOUTUBE_API_KEY_POOL_JSON", '["key1", "key2", "key3"]')
    monkeypatch.setenv("COMPLIANCE_MODE", "false")

    from atlas.config import Settings

    settings = Settings()

    keys = settings.api_keys
    assert len(keys) == 3


def test_vault_validation_hf(monkeypatch):
    """Test HuggingFace vault requires proper config."""
    import os
    import subprocess
    import sys

    # Get the parent directory where .env file is located (atlas root)
    atlas_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(atlas_root, ".env")
    env_backup = os.path.join(atlas_root, ".env.bak")

    # Rename .env file to prevent pydantic-settings from loading it
    if os.path.exists(env_file):
        os.rename(env_file, env_backup)

    try:
        # Create a test script that will run in isolation
        test_script = """
import os
import sys
# Clear any existing environment variables that might interfere
os.environ.pop("HF_TOKEN", None)
os.environ.pop("HF_DATASET_ID", None)
os.environ["VAULT_PROVIDER"] = "huggingface"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["YOUTUBE_API_KEY_POOL_JSON"] = '["test_key"]'
os.environ["COMPLIANCE_MODE"] = "false"
os.environ["ENV"] = "test"
os.environ["JANITOR_ENABLED"] = "false"
os.environ["JANITOR_RETENTION_DAYS"] = "7"
# HF_TOKEN and HF_DATASET_ID are NOT set, which should trigger ValidationError

from pydantic import ValidationError

# Try to import atlas.config - this will raise ValidationError at module level
# if HF_TOKEN and HF_DATASET_ID are not set
try:
    from atlas.config import Settings
    print("ERROR: Import succeeded but should have raised ValidationError")
    sys.exit(1)
except (ValidationError, ImportError) as e:
    # Check if it's the expected validation error
    error_str = str(e)
    if "HF_DATASET_ID and HF_TOKEN required for HuggingFace vault" in error_str:
        print("SUCCESS: ValidationError raised as expected during import")
        sys.exit(0)
    elif isinstance(e, ImportError) and "ValidationError" in error_str:
        # Pydantic wraps ValidationError in ImportError on some versions
        print("SUCCESS: ValidationError wrapped in ImportError as expected")
        sys.exit(0)
    else:
        print(f"ERROR: Unexpected error: {e}")
        sys.exit(1)
"""

        # Run the test script in a subprocess with proper PYTHONPATH
        result = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            cwd=atlas_root,
            env={**os.environ, "PYTHONPATH": os.path.join(atlas_root, "src")},
        )

        # Report the results
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        assert result.returncode == 0, "Test failed - see output above"

    finally:
        # Restore the .env file regardless of test outcome
        if os.path.exists(env_backup):
            os.rename(env_backup, env_file)


def test_vault_validation_gcs(test_env, monkeypatch):
    """Test GCS vault requires proper config."""
    monkeypatch.setenv("VAULT_PROVIDER", "gcs")
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)

    from atlas.config import Settings

    with pytest.raises(ValidationError):
        Settings()
