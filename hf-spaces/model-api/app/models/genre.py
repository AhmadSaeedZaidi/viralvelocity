import logging

import joblib
import numpy as np
from huggingface_hub import hf_hub_download
from tensorflow.keras.models import load_model

from ..core.config import settings
from ..schemas import GenreInput
from .base import BaseModelWrapper

logger = logging.getLogger("YoutubeML-Models")


class GenreClassifier(BaseModelWrapper):
    def __init__(self, name: str, repo_path: str):
        """
        repo_path is ignored here because we have multiple files.
        We hardcode the paths relative to the repo root for this specific model.
        """
        super().__init__(name, repo_path)
        self.vectorizer = None
        self.pca = None
        self.label_encoder = None

    def load(self):
        """Loads all components from HF Hub."""
        try:
            # Define the components we need
            components = {
                "model": "genre/model.h5",
                "vectorizer": "genre/vectorizer.pkl",
                "label_encoder": "genre/label_encoder.pkl",
                "svd": "genre/svd.pkl",
            }

            paths = {}
            for key, repo_file in components.items():
                logger.info(f"Downloading {key} from {repo_file}...")
                paths[key] = hf_hub_download(
                    repo_id=f"{settings.HF_USERNAME}/{settings.HF_MODEL_REPO}",
                    filename=repo_file,
                    token=settings.HF_TOKEN or None,
                    cache_dir=settings.MODEL_DIR,
                )

            # Load artifacts
            self.model = load_model(paths["model"])
            self.vectorizer = joblib.load(paths["vectorizer"])
            self.label_encoder = joblib.load(paths["label_encoder"])
            self.pca = joblib.load(paths["svd"])

            self.is_loaded = True
            logger.info("Successfully loaded GenreClassifier components.")

        except Exception as e:
            logger.warning(f"Failed to load GenreClassifier: {e}")
            if settings.ENABLE_MOCK_INFERENCE:
                self._init_mock_model()
                self.is_loaded = True
            else:
                raise

    def _init_mock_model(self):
        from sklearn.decomposition import IncrementalPCA
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.neural_network import MLPClassifier

        self.vectorizer = TfidfVectorizer(max_features=5000, stop_words="english")
        self.pca = IncrementalPCA(n_components=5)
        # We use a simple MLP to mimic the Keras model interface for the mock
        self.mock_mlp = MLPClassifier(hidden_layer_sizes=(50,), max_iter=10)

        texts = [
            "gaming minecraft",
            "tutorial python",
            "vlog daily",
            "makeup tutorial",
        ] * 5
        labels = ["Gaming", "Tutorial", "Vlog", "Lifestyle"] * 5

        X_sparse = self.vectorizer.fit_transform(texts)
        X_dense = self.pca.fit_transform(X_sparse.toarray())
        self.mock_mlp.fit(X_dense, labels)

        # Mock label encoder
        class MockLE:
            def inverse_transform(self, idx):
                return [labels[i] for i in idx]

        self.label_encoder = MockLE()
        self.model = None  # We use mock_mlp instead

    def predict(self, input_data: GenreInput):
        text_data = f"{input_data.title} {' '.join(input_data.tags)}"

        if self.model:
            # Real Keras Model
            vec = self.vectorizer.transform([text_data])
            reduced = self.pca.transform(vec)  # TruncatedSVD supports sparse input

            # Keras model expects dense input usually, but let's check pipeline
            # Pipeline: X_train_red = svd.fit_transform(X_train_tfidf)
            # So input to model is output of SVD.

            probs = self.model.predict(reduced)[0]
            pred_idx = np.argmax(probs)
            confidence = float(probs[pred_idx])
            pred_label = self.label_encoder.inverse_transform([pred_idx])[0]

            return pred_label, confidence

        else:
            # Mock Model
            vec = self.vectorizer.transform([text_data])
            reduced = self.pca.transform(vec.toarray())

            pred = self.mock_mlp.predict(reduced)[0]
            probs = self.mock_mlp.predict_proba(reduced)[0]
            confidence = float(max(probs))

            return pred, confidence
