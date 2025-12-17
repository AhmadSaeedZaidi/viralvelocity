from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/v1/models", tags=["Models"])


@router.get("/status", response_model=Dict[str, Any])
async def get_models_status(request: Request):
    """
    Returns the loading status and internal class type of all registered models.
    Useful for the Streamlit dashboard 'Model Config' page.
    """
    status = {}
    if hasattr(request.app.state, "models"):
        for name, wrapper in request.app.state.models.items():
            status[name] = {
                "loaded": wrapper.is_loaded,
                "type": wrapper.__class__.__name__,
                "backend": "mock" if getattr(wrapper, "is_mock", False) else "joblib",
            }
    return status


@router.get("/{model_name}/explain", response_model=Dict[str, float])
async def explain_model(model_name: str, request: Request):
    """
    Returns feature importance for the specified model if available.
    """
    if not hasattr(request.app.state, "models"):
        raise HTTPException(status_code=503, detail="Models not initialized")

    model = request.app.state.models.get(model_name)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    return model.get_feature_importance()
