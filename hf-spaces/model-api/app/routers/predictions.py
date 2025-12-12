import time

from fastapi import APIRouter, HTTPException, Request
from ..schemas import (
    AnomalyInput,
    ClickbaitInput,
    GenreInput,
    PredictionResponse,
    TagInput,
    VelocityInput,
    ViralInput,
)

router = APIRouter(prefix="/api/v1/predict", tags=["Predictions"])

@router.post("/velocity", response_model=PredictionResponse)
async def predict_velocity(input_data: VelocityInput, request: Request):
    model = request.app.state.models.get("velocity")
    if not model:
        raise HTTPException(status_code=503, detail="Velocity model not available")
        
    start_time = time.time()
    prediction = model.predict(input_data)
    duration = (time.time() - start_time) * 1000
    
    return {
        "model_name": "Velocity Predictor",
        "prediction": prediction,
        "processing_time_ms": duration,
        "metadata": {"unit": "views_7_days"}
    }

@router.post("/clickbait", response_model=PredictionResponse)
async def predict_clickbait(input_data: ClickbaitInput, request: Request):
    model = request.app.state.models.get("clickbait")
    if not model:
        raise HTTPException(status_code=503, detail="Clickbait model not available")

    start_time = time.time()
    label, prob = model.predict(input_data)
    duration = (time.time() - start_time) * 1000
    
    return {
        "model_name": "Clickbait Detector",
        "prediction": "Clickbait" if label == 1 else "Solid",
        "probability": prob,
        "processing_time_ms": duration
    }

@router.post("/genre", response_model=PredictionResponse)
async def predict_genre(input_data: GenreInput, request: Request):
    model = request.app.state.models.get("genre")
    if not model:
        raise HTTPException(status_code=503, detail="Genre model not available")

    start_time = time.time()
    genre, confidence = model.predict(input_data)
    duration = (time.time() - start_time) * 1000
    
    return {
        "model_name": "Genre Classifier (PCA+MLP)",
        "prediction": genre,
        "confidence_score": confidence,
        "processing_time_ms": duration
    }

@router.post("/tags", response_model=PredictionResponse)
async def predict_tags(input_data: TagInput, request: Request):
    model = request.app.state.models.get("tags")
    if not model:
        raise HTTPException(status_code=503, detail="Tags model not available")

    start_time = time.time()
    recs = model.predict(input_data)
    duration = (time.time() - start_time) * 1000
    
    return {
        "model_name": "Tag Association Rules",
        "prediction": recs,
        "processing_time_ms": duration
    }

@router.post("/viral", response_model=PredictionResponse)
async def predict_viral(input_data: ViralInput, request: Request):
    model = request.app.state.models.get("viral")
    if not model:
        raise HTTPException(status_code=503, detail="Viral model not available")

    start_time = time.time()
    is_viral, prob = model.predict(input_data)
    duration = (time.time() - start_time) * 1000
    
    return {
        "model_name": "Viral Trend Classifier",
        "prediction": "Viral" if is_viral == 1 else "Normal",
        "probability": prob,
        "processing_time_ms": duration
    }

@router.post("/anomaly", response_model=PredictionResponse)
async def predict_anomaly(input_data: AnomalyInput, request: Request):
    model = request.app.state.models.get("anomaly")
    if not model:
        raise HTTPException(status_code=503, detail="Anomaly model not available")

    start_time = time.time()
    is_anomaly, score = model.predict(input_data)
    duration = (time.time() - start_time) * 1000
    
    return {
        "model_name": "Anomaly Detector (Isolation Forest)",
        "prediction": "ANOMALY DETECTED" if is_anomaly else "Normal Data",
        "confidence_score": score,
        "processing_time_ms": duration,
        "metadata": {"details": "Score < 0 indicates anomaly"}
    }