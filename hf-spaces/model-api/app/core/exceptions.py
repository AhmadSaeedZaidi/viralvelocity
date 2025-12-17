from fastapi import Request
from fastapi.responses import JSONResponse


class ModelError(Exception):
    """Base exception for model-related errors"""

    pass


class ModelNotLoadedError(ModelError):
    """Raised when trying to predict with a model that isn't loaded"""

    pass


class PredictionError(ModelError):
    """Raised when the inference step fails"""

    pass


async def model_exception_handler(request: Request, exc: ModelError):
    """
    Global exception handler for the FastAPI app.
    Add this to app.py: app.add_exception_handler(ModelError, model_exception_handler)
    """
    return JSONResponse(
        status_code=500,
        content={
            "error": exc.__class__.__name__,
            "message": str(exc),
            "path": request.url.path,
        },
    )
