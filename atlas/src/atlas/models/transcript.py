from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Transcript(BaseModel):  # type: ignore[misc]
    video_id: str
    language: str = "en"
    vault_uri: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
