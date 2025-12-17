import logging
import os
from typing import Any, Optional

import joblib

logger = logging.getLogger("YoutubeML-Utils")


def load_pickle_model(filepath: str) -> Optional[Any]:
    """
    Safely loads a pickle file with error handling.
    """
    if not os.path.exists(filepath):
        logger.warning(f"File not found: {filepath}")
        return None

    try:
        model = joblib.load(filepath)
        logger.info(f"Successfully loaded model from {filepath}")
        return model
    except Exception as e:
        logger.error(f"Corrupt or incompatible model file at {filepath}: {e}")
        return None


def get_model_size_mb(filepath: str) -> float:
    """
    Returns the size of the model file in MB.
    Useful for the dashboard to monitor memory usage.
    """
    if not os.path.exists(filepath):
        return 0.0
    return os.path.getsize(filepath) / (1024 * 1024)
