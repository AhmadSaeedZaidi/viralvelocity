import os
from typing import Any, Dict

import requests
import streamlit as st

# Default to local uvicorn port
DEFAULT_API_URL = "http://localhost:8000"

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

def _fix_hf_url(url: str) -> str:
    """Converts HF Spaces web URL to direct API URL if needed."""
    # Example: https://huggingface.co/spaces/Rolaficus/ViralVelocity-api 
    # Becomes: https://rolaficus-viralvelocity-api.hf.space
    if "huggingface.co/spaces/" in url:
        try:
            # Remove protocol and split
            clean_url = url.replace("https://", "").replace("http://", "")
            parts = clean_url.split("huggingface.co/spaces/")[-1].split("/")
            if len(parts) >= 2:
                user = parts[0]
                space = parts[1]
                # HF Spaces URLs are lowercase and use hyphens
                return f"https://{user.lower()}-{space.lower()}.hf.space"
        except Exception:
            return url # Fallback to original if parsing fails
    return url

class YoutubeMLClient:
    def __init__(self):
        raw_url = get_api_url()
        self.base_url = _fix_hf_url(raw_url).rstrip("/")
        # Optional: Display connected API in sidebar for debugging
        # st.sidebar.caption(f"API: {self.base_url}")
    
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