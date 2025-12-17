import logging
import os
from typing import Any

import joblib
from huggingface_hub import hf_hub_download
from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError

from ..core.config import settings

logger = logging.getLogger("YoutubeML-Models")


class BaseModelWrapper:
    def __init__(self, name: str, repo_path: str):
        """
        Args:
            name: Internal name for logging (e.g., "velocity_v1")
            repo_path: Path to the file in the HF repo (e.g., "velocity/model.pkl")
        """
        self.name = name
        self.repo_path = repo_path
        self.model = None
        self.is_loaded = False

    def load(self):
        """Loads model from HF Hub or initializes a mock for the demo."""
        try:
            if os.path.isabs(self.repo_path):
                raise EntryNotFoundError(
                    f"Local/absolute repo_path '{self.repo_path}' is not a Hub filename"
                )

            # Ensure cache dir is present and writable. Spaces often have read-only
            # repo FS.
            cache_dir = settings.MODEL_DIR
            try:
                os.makedirs(cache_dir, exist_ok=True)
                testfile = os.path.join(cache_dir, ".write_test")
                with open(testfile, "w", encoding="utf-8") as f:
                    f.write("ok")
                os.remove(testfile)
            except Exception as dir_err:
                fallback = "/tmp/hf_hub_cache"
                logger.warning(
                    "Configured MODEL_DIR '%s' is not writable (%s). "
                    "Falling back to '%s'.",
                    cache_dir,
                    dir_err,
                    fallback,
                )
                os.makedirs(fallback, exist_ok=True)
                cache_dir = fallback

            # 1. Try to download from Hugging Face Hub
            logger.info(
                f"Attempting to download {self.name} from HF Hub: "
                f"{settings.HF_USERNAME}/{settings.HF_MODEL_REPO} -> {self.repo_path}"
            )

            model_path = hf_hub_download(
                repo_id=f"{settings.HF_USERNAME}/{settings.HF_MODEL_REPO}",
                filename=self.repo_path,
                token=settings.HF_TOKEN or None,  # Use token if available, else public
                cache_dir=cache_dir,
            )

            self.model = joblib.load(model_path)
            self.is_loaded = True
            logger.info(f"Successfully loaded real model: {self.name}")

        except (EntryNotFoundError, RepositoryNotFoundError, Exception) as e:
            logger.warning(
                "Failed to load %s from Hub (repo=%s/%s, path=%s): %s",
                self.name,
                settings.HF_USERNAME,
                settings.HF_MODEL_REPO,
                self.repo_path,
                e,
            )

            if settings.ENABLE_MOCK_INFERENCE:
                logger.info(f"Initializing MOCK for {self.name}.")
                self._init_mock_model()
                setattr(self, "is_mock", True)
                self.is_loaded = True
            else:
                raise FileNotFoundError(
                    f"Model {self.name} not found and mocks disabled."
                )

    def _init_mock_model(self):
        """Initializes a dummy model for valid API responses."""
        pass

    def predict(self, data: Any) -> Any:
        raise NotImplementedError
