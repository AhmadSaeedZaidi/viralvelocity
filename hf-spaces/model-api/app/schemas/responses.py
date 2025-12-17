from typing import Any, Dict, Optional

from pydantic import BaseModel


class PredictionResponse(BaseModel):
    model_name: str
    prediction: Any
    probability: Optional[float] = None
    confidence_score: Optional[float] = None
    processing_time_ms: float
    metadata: Dict[str, Any] = {}
