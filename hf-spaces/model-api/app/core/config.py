import os

from pydantic import BaseModel


class Settings(BaseModel):
    # API Settings
    PROJECT_NAME: str = "YouTube ML Microservice"
    API_V1_STR: str = "/api/v1"
    VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    
    # Model Settings
    MODEL_DIR: str = os.getenv("MODEL_DIR", "models_storage")
    ENABLE_MOCK_INFERENCE: bool = (
        os.getenv("ENABLE_MOCK_INFERENCE", "True").lower() == "true"
    )
    
    # Hardware/Performance
    MAX_THREADS: int = int(os.getenv("MAX_THREADS", "2"))
    
    # Security (If you add API keys later)
    API_KEY: str = os.getenv("API_KEY", "")

settings = Settings()