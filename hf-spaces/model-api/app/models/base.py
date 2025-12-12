import logging
import os
from typing import Any

import joblib

logger = logging.getLogger("YoutubeML-Models")

MODEL_DIR = "models_storage"
ENABLE_MOCK_INFERENCE = True

class BaseModelWrapper:
    def __init__(self, name: str):
        self.name = name
        self.model = None
        self.is_loaded = False

    def load(self):
        """Loads model from disk or initializes a mock for the demo."""
        try:
            path = os.path.join(MODEL_DIR, f"{self.name}.pkl")
            if os.path.exists(path):
                self.model = joblib.load(path)
                self.is_loaded = True
                logger.info(f"Loaded real model: {self.name}")
            elif ENABLE_MOCK_INFERENCE:
                logger.info(f"Model {self.name} not found. Initializing MOCK.")
                self._init_mock_model()
                self.is_loaded = True
            else:
                raise FileNotFoundError(f"Model file {path} not found.")
        except Exception as e:
            logger.error(f"Failed to load {self.name}: {e}")
            raise

    def _init_mock_model(self):
        """Initializes a dummy model for valid API responses."""
        pass

    def predict(self, data: Any) -> Any:
        raise NotImplementedError