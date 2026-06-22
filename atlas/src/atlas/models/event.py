from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class SystemEvent(BaseModel):
    id: str
    event_type: str
    entity_id: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
