from fastapi import APIRouter, Request

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check(request: Request):
    """
    Simple heartbeat endpoint.
    """
    models_loaded = (
        len(request.app.state.models) if hasattr(request.app.state, "models") else 0
    )
    return {
        "status": "healthy",
        "service": "youtube-ml-api",
        "models_loaded": models_loaded
    }