import json
from typing import List, Optional, Literal
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, Field, SecretStr, field_validator, model_validator


class Settings(BaseSettings):
    DATABASE_URL: PostgresDsn = Field(..., description="Neon/Postgres Connection String")
    VAULT_PROVIDER: Literal["huggingface", "gcs"] = Field(
        "huggingface", description="Storage Backend Provider"
    )
    
    HF_DATASET_ID: Optional[str] = Field(None, description="HF Dataset ID (username/dataset)")
    HF_TOKEN: Optional[SecretStr] = Field(None, description="HF Write Token")
    GCS_BUCKET_NAME: Optional[str] = Field(None, description="GCS Bucket Name")
    
    COMPLIANCE_MODE: bool = Field(True, description="Enforce API Policy limits")
    ENV: str = Field("dev", description="Deployment environment (dev/prod)")
    
    YOUTUBE_API_KEY_POOL_JSON: SecretStr = Field(
        ..., description="JSON List of YouTube API Keys"
    )
    
    DISCORD_WEBHOOK_ALERTS: Optional[SecretStr] = None
    DISCORD_WEBHOOK_HUNT: Optional[SecretStr] = None
    DISCORD_WEBHOOK_SURVEILLANCE: Optional[SecretStr] = None
    DISCORD_WEBHOOK_OPS: Optional[SecretStr] = None
    
    PREFECT_API_URL: Optional[str] = None
    PREFECT_API_KEY: Optional[SecretStr] = None
    
    @model_validator(mode="after")
    def validate_vault_config(self) -> "Settings":
        if self.VAULT_PROVIDER == "huggingface":
            if not self.HF_DATASET_ID or not self.HF_TOKEN:
                raise ValueError(
                    "HF_DATASET_ID and HF_TOKEN required for HuggingFace vault"
                )
        elif self.VAULT_PROVIDER == "gcs":
            if not self.GCS_BUCKET_NAME:
                raise ValueError("GCS_BUCKET_NAME required for GCS vault")
        return self
    
    @property
    def api_keys(self) -> List[str]:
        try:
            payload = self.YOUTUBE_API_KEY_POOL_JSON.get_secret_value()
            keys = json.loads(payload)
            if isinstance(keys, str):
                keys = [keys]
            
            if self.COMPLIANCE_MODE:
                return keys[:1]
            return keys
        except json.JSONDecodeError:
            return [self.YOUTUBE_API_KEY_POOL_JSON.get_secret_value()]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()