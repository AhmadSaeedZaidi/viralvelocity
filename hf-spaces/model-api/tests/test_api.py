import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    # Enable mock inference for API tests so we don't need real models
    with patch("app.models.base.settings.ENABLE_MOCK_INFERENCE", True):
        with TestClient(app) as c:
            yield c


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "models_loaded" in data


def test_model_status(client):
    response = client.get("/api/v1/models/status")
    assert response.status_code == 200
    data = response.json()
    assert "velocity" in data
    assert data["velocity"]["loaded"] is True


def test_predict_velocity_endpoint(client):
    payload = {
        "log_start_views": 6.9,
        "log_duration": 5.7,
        "initial_virality_slope": 1.2,
        "interaction_density": 0.1,
        "like_view_ratio": 0.05,
        "comment_view_ratio": 0.01,
        "video_age_hours": 2.0,
        "hour_sin": 0.5,
        "hour_cos": -0.8,
        "publish_day": 1,
        "is_weekend": 0,
        "title_len": 50,
        "caps_ratio": 0.1,
        "exclamation_count": 1,
        "question_count": 0,
        "has_digits": 0,
        "category_id": 10,
    }
    response = client.post("/api/v1/predict/velocity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["model_name"] == "Velocity Predictor"
    assert "prediction" in data
    assert "processing_time_ms" in data


def test_predict_clickbait_endpoint(client):
    payload = {
        "title": "Shocking Video",
        "view_count": 5000,
        "like_count": 10,
        "comment_count": 2,
    }
    response = client.post("/api/v1/predict/clickbait", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["prediction"] in ["Clickbait", "Solid"]


def test_predict_tags_endpoint(client):
    payload = {"current_tags": ["python", "tutorial"]}
    response = client.post("/api/v1/predict/tags", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["prediction"], list)


def test_invalid_input_validation(client):
    # Sending string instead of int for view_count
    payload = {
        "title": "Bad Request",
        "view_count": "not_a_number",
        "like_count": 10,
        "comment_count": 2,
    }
    response = client.post("/api/v1/predict/clickbait", json=payload)
    assert response.status_code == 422  # Unprocessable Entity
