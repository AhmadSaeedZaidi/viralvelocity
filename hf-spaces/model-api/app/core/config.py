import os

from pydantic import BaseModel


def _default_model_dir() -> str:
    """Return a writable cache directory for model downloads.

    Priority order:
    1) MODEL_DIR (explicit)
    2) HF_HUB_CACHE (hf hub cache)
    3) HF_HOME + "/hub" (hf home)
    4) /tmp/hf_hub_cache
    """
    explicit = os.getenv("MODEL_DIR")
    if explicit:
        return explicit

    hf_hub_cache = os.getenv("HF_HUB_CACHE")
    if hf_hub_cache:
        return hf_hub_cache

    hf_home = os.getenv("HF_HOME")
    if hf_home:
        return os.path.join(hf_home, "hub")

    return "/tmp/hf_hub_cache"


class Settings(BaseModel):
    # API Settings
    PROJECT_NAME: str = "YouTube ML Microservice"
    API_V1_STR: str = "/api/v1"
    VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"

    # Model Settings
    MODEL_DIR: str = _default_model_dir()
    ENABLE_MOCK_INFERENCE: bool = (
        os.getenv("ENABLE_MOCK_INFERENCE", "True").lower() == "true"
    )

    # Hardware/Performance
    MAX_THREADS: int = int(os.getenv("MAX_THREADS", "2"))

    # Security (If you add API keys later)
    API_KEY: str = os.getenv("API_KEY", "")

    # Hugging Face Model Registry
    HF_TOKEN: str = os.getenv("HF_TOKEN", "")
    HF_USERNAME: str = os.getenv("HF_USERNAME", "Rolaficus")
    HF_MODEL_REPO: str = os.getenv("HF_MODEL_REPO", "ViralVelocity-models")


settings = Settings()
os.environ.setdefault("HF_HOME", "/tmp/hf")
os.environ.setdefault("HF_HUB_CACHE", settings.MODEL_DIR)
