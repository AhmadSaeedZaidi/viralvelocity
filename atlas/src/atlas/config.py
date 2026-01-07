import json
from typing import List, Optional, Literal
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, Field, SecretStr

class Settings(BaseSettings):
    """
    Atlas Configuration Kernel.
    Reads from environment variables or .env file.
    """
    
    # --- Infrastructure Connections ---
    DATABASE_URL: PostgresDsn = Field(..., description="Neon/Postgres Connection String")
    
    # --- Storage Switch ---
    VAULT_PROVIDER: Literal["huggingface", "gcs"] = Field("huggingface", description="Storage Backend Provider")
    
    # --- Provider Specifics ---
    HF_DATASET_ID: Optional[str] = Field(None, description="HF Dataset ID (username/dataset)")
    HF_TOKEN: Optional[SecretStr] = Field(None, description="HF Write Token")
    
    # For Google Cloud Storage
    GCS_BUCKET_NAME: Optional[str] = Field(None, description="GCS Bucket Name")
    
    # --- Governance & Compliance ---
    COMPLIANCE_MODE: bool = Field(True, description="Enforce API Policy limits")
    
    # --- Environment ---
    ENV: str = Field("dev", description="Deployment environment (dev/prod)")
    
    # --- Secrets ---
    YOUTUBE_API_KEY_POOL_JSON: SecretStr = Field(..., description="JSON List of YouTube API Keys")
    
    # --- Observability (Nervous System) ---
    DISCORD_WEBHOOK_ALERTS: Optional[SecretStr] = None
    DISCORD_WEBHOOK_HUNT: Optional[SecretStr] = None
    DISCORD_WEBHOOK_SURVEILLANCE: Optional[SecretStr] = None
    DISCORD_WEBHOOK_OPS: Optional[SecretStr] = None
    
    # Prefect Orchestration (Optional)
    PREFECT_API_URL: Optional[str] = None
    PREFECT_API_KEY: Optional[SecretStr] = None
    
    @property
    def api_keys(self) -> List[str]:
        """
        Parses the key pool. 
        If Compliance Mode is ON, strictly returns only the first key 
        to demonstrate adherence to standard quotas.
        """
        try:
            payload = self.YOUTUBE_API_KEY_POOL_JSON.get_secret_value()
            keys = json.loads(payload)
            if isinstance(keys, str): keys = [keys]
            
            if self.COMPLIANCE_MODE:
                return keys[:1]
            return keys
        except Exception:
            return [self.YOUTUBE_API_KEY_POOL_JSON.get_secret_value()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"

settings = Settings()