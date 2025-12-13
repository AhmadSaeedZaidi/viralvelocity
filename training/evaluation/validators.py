import os
import joblib
import pandas as pd
from huggingface_hub import hf_hub_download
from sklearn.metrics import f1_score, r2_score, accuracy_score
from prefect import get_run_logger

class ModelValidator:
    def __init__(self, repo_id: str, local_dir: str = "./temp_models"):
        self.repo_id = repo_id
        self.local_dir = local_dir
        os.makedirs(self.local_dir, exist_ok=True)
        self.logger = get_run_logger()

    def load_production_model(self, model_filename: str):
        """Downloads the current model from HF Hub. Returns None if not found."""
        try:
            model_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=model_filename,
                local_dir=self.local_dir
            )
            self.logger.info(f"Loaded production model from {model_path}")
            return joblib.load(model_path)
        except Exception:
            self.logger.warning("No production model found (or download failed). This is likely the first run.")
            return None

    def compare_models(self, new_model, old_model, X_test, y_test, metric_name="f1_score"):
        """
        Compares New vs Old.
        Returns: (passed_bool, new_score, old_score)
        """
        # Get Scores
        new_score = self._score(new_model, X_test, y_test, metric_name)
        
        if old_model is None:
            self.logger.info(f"No old model to compare. New model wins default. Score: {new_score}")
            return True, new_score, 0.0

        old_score = self._score(old_model, X_test, y_test, metric_name)
        
        # Comparison logic (higher is better for F1/R2/Acc)
        # Note: If metric is MSE/MAE, logic needs to be reversed (lower is better)
        improvement = new_score - old_score
        
        self.logger.info(f"Comparison Result: New={new_score:.4f}, Old={old_score:.4f}, Diff={improvement:.4f}")
        
        # Improvement Threshold (e.g., must be at least equal or better)
        if new_score >= old_score:
            return True, new_score, old_score
        else:
            return False, new_score, old_score

    def _score(self, model, X, y, metric_name):
        preds = model.predict(X)
        if metric_name == "f1_score":
            return f1_score(y, preds)
        elif metric_name == "r2_score":
            return r2_score(y, preds)
        elif metric_name == "accuracy":
            return accuracy_score(y, preds)
        else:
            raise ValueError(f"Unknown metric: {metric_name}")