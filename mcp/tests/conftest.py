"""Set required Atlas env vars before any pleiades_mcp import (collection time)."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
os.environ.setdefault("YOUTUBE_API_KEY_POOL_JSON", '["k1"]')
os.environ.setdefault("VAULT_PROVIDER", "huggingface")
os.environ.setdefault("HF_DATASET_ID", "mock/ds")
os.environ.setdefault("HF_TOKEN", "mock")
