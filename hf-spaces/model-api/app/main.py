import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .core.config import settings
from .core.exceptions import ModelError, model_exception_handler
from .models import (
    AnomalyDetector,
    ClickbaitDetector,
    GenreClassifier,
    TagRecommender,
    VelocityPredictor,
    ViralTrendPredictor,
)

# Import the routers we created
from .routers import health, metrics, models, predictions

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("YoutubeML-API")

# --- App Lifecycle & State ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On Startup: Load all models and attach them to app.state.
    On Shutdown: Clear memory.
    """
    logger.info("Starting up... Initializing models.")
    
    # Initialize instances with their HF Hub paths
    model_instances = {
        "velocity": VelocityPredictor("velocity_v1", "velocity/model.pkl"),
        "clickbait": ClickbaitDetector("clickbait_v1", "clickbait/model.pkl"),
        # Note: Genre needs special handling for multiple files
        "genre": GenreClassifier("genre_v1", "genre/model.h5"),
        "tags": TagRecommender("tags_v1", "tags/rules.pkl"),
        "viral": ViralTrendPredictor("viral_v1", "viral/model.pkl"),
        "anomaly": AnomalyDetector("anomaly_v1", "anomaly/model.pkl"),
    }

    load_errors: dict[str, str] = {}
    for name, model in model_instances.items():
        try:
            model.load()
            logger.info("Model %s ready.", name)
        except Exception as e:
            load_errors[name] = str(e)
            logger.exception("Model %s failed to load at startup: %s", name, e)
    
    # Attach to app state for access in Routers
    app.state.models = model_instances
    app.state.model_load_errors = load_errors
    if load_errors:
        logger.warning(
            "Startup completed with %d model load failures. Health will report degraded state.",
            len(load_errors),
        )
    
    yield
    
    # Cleanup
    app.state.models.clear()
    if hasattr(app.state, "model_load_errors"):
        app.state.model_load_errors.clear()
    logger.info("Shutting down... Models cleared.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Hosting 6 custom ML models for YouTube analytics on HF Spaces.",
    version=settings.VERSION,
    lifespan=lifespan
)

# --- Error Handlers ---
app.add_exception_handler(ModelError, model_exception_handler)

# --- Mount Routers ---
app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(models.router)
app.include_router(predictions.router)

# Root endpoint for convenience
@app.get("/", tags=["Info"])
def root():
    return {
        "message": "Welcome to ViralVelocity Model API", 
        "docs": "/docs",
        "health": "/health"
    }