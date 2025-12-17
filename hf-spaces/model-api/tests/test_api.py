import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
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
        "video_stats_24h": {
            "view_count": 1000,
            "like_count": 100,
            "comment_count": 50,
            "duration_seconds": 300,
            "published_hour": 12,
            "published_day_of_week": 1,
        },
        "channel_stats": {
            "id": "channel_1",
            "avg_views_last_5": 2000,
            "subscriber_count": 500,
        },
        "slope_views": 10.0,
        "slope_engagement": 0.5,
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
