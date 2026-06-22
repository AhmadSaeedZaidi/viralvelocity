from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class SearchQueueItem(BaseModel):
    id: int
    query_term: str
    priority: int = 0
    mention_count: int = 0
    next_page_token: Optional[str] = None
    last_searched_at: Optional[datetime] = None
    result_count_total: Optional[int] = 0
    status: str = "active"

    model_config = ConfigDict(from_attributes=True)
