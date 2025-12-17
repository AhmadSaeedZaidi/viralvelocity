from fastapi import APIRouter, Request

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check(request: Request):
    """
    Simple heartbeat endpoint.
    """
    models_loaded = (
        len(request.app.state.models)
        if hasattr(request.app.state, "models")
        else 0
    )
    load_errors = (
        request.app.state.model_load_errors
        if hasattr(request.app.state, "model_load_errors")
        else {}
    )

    status = "healthy" if not load_errors else "degraded"
    return {
        "status": status,
        "service": "youtube-ml-api",
        "models_loaded": models_loaded,
        "model_load_errors": load_errors,
    }