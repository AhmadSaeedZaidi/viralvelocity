from .health import router as health_router
from .metrics import router as metrics_router
from .models import router as models_router
from .predictions import router as predictions_router

__all__ = [
    "predictions_router", 
    "models_router", 
    "metrics_router", 
    "health_router"
]