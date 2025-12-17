import os
from typing import Any, Dict

import requests
import streamlit as st

# Default to local if not set, or use the public HF Space URL
DEFAULT_API_URL = "http://localhost:7860"

def get_api_url() -> str:
    """Retrieves the API URL from secrets or environment variables."""
    # Check Streamlit secrets first (for HF deployment), then OS env, then default
    try:
        if "API_URL" in st.secrets:
            return st.secrets["API_URL"]
    except FileNotFoundError:
        pass  # Secrets file not found, fall back to env vars
    except Exception:
        pass # Handle other potential secrets errors gracefully

    return os.getenv("API_URL", DEFAULT_API_URL)

class YoutubeMLClient:
    def __init__(self):
        self.base_url = get_api_url().rstrip("/")
    
    def _post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/predict/{endpoint}",
                json=data,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            st.error(f"API Connection Error: {e}")
            return None

    def _get(self, endpoint: str) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.base_url}/api/v1/{endpoint}", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            st.error(f"Failed to fetch {endpoint}: {e}")
            return {}

    # --- Prediction Methods ---

    def predict_velocity(self, data: Dict[str, Any]):
        return self._post("velocity", data)

    def predict_clickbait(self, data: Dict[str, Any]):
        return self._post("clickbait", data)

    def predict_genre(self, data: Dict[str, Any]):
        return self._post("genre", data)

    def predict_tags(self, data: Dict[str, Any]):
        return self._post("tags", data)

    def predict_viral(self, data: Dict[str, Any]):
        return self._post("viral", data)

    def predict_anomaly(self, data: Dict[str, Any]):
        return self._post("anomaly", data)

    # --- System Methods ---
    
    def get_health(self):
        try:
            return requests.get(f"{self.base_url}/health", timeout=3).json()
        except requests.exceptions.RequestException:
            return {"status": "offline"}

    def get_model_status(self):
        return self._get("models/status")
    
    def get_metrics(self):
        return self._get("metrics")