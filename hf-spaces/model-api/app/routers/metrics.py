from typing import Any, Dict, List

import numpy as np
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)

router = APIRouter(prefix="/api/v1", tags=["Metrics"])


class EvaluationRequest(BaseModel):
    y_true: List[Any]
    y_pred: List[Any]
    task_type: str = "regression"  # regression, classification, binary


@router.post("/evaluate", response_model=Dict[str, Any])
async def evaluate_predictions(payload: EvaluationRequest):
    """
    Calculates standard ML metrics for a given set of true and predicted values.
    Used by the dashboard to compute performance on live data.
    """
    y_true = np.array(payload.y_true)
    y_pred = np.array(payload.y_pred)

    metrics = {}

    if payload.task_type == "regression":
        # Avoid division by zero for MAPE
        y_true_safe = np.where(y_true == 0, 1, y_true)
        mape = np.mean(np.abs((y_true - y_pred) / y_true_safe)) * 100

        metrics = {
            "mae": float(mean_absolute_error(y_true, y_pred)),
            "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
            "r2": float(r2_score(y_true, y_pred)),
            "mape": float(mape),
        }

    elif payload.task_type in ["classification", "binary"]:
        average = "binary" if payload.task_type == "binary" else "weighted"
        
        try:
            y_pred_float = y_pred.astype(float)
            y_pred_class = np.round(y_pred_float)
        except (ValueError, TypeError):
            # If conversion fails, they are likely string labels
            y_pred_class = y_pred

        metrics = {
            "accuracy": float(accuracy_score(y_true, y_pred_class)),
            "f1": float(
                f1_score(y_true, y_pred_class, average=average, zero_division=0)
            ),
            "precision": float(
                precision_score(y_true, y_pred_class, average=average, zero_division=0)
            ),
            "recall": float(
                recall_score(y_true, y_pred_class, average=average, zero_division=0)
            ),
        }

    return metrics


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
        "environment": "huggingface_spaces",
    }
