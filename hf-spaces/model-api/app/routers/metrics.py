from typing import Any, Dict

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1", tags=["Metrics"])

@router.get("/metrics", response_model=Dict[str, Any])
async def get_metrics(request: Request):
    """
    Exposes basic runtime metrics.
    Connect this to your 'monitor-drift.yml' workflow or a scraping tool.
    """
    model_count = (
        len(request.app.state.models) if hasattr(request.app.state, "models") else 0
    )
    
    return {
        "service_status": "active",
        "models_active": model_count,
        "version": "1.0.0",
        "environment": "huggingface_spaces"
    }