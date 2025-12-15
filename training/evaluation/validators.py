import os

import joblib
from huggingface_hub import hf_hub_download
from prefect import get_run_logger
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)


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
        except Exception as exc:
            # Log the exception for debugging while avoiding a bare except
            self.logger.warning(
                "No production model found (or download failed). "
                "This is likely the first run."
            )
            self.logger.debug("Model download/load error: %s", exc)
            return None

    def compare_models(
        self,
        new_model,
        old_model,
        X_test,
        y_test,
        metric_name="f1_score",
    ):
        """
        Compares New vs Old.
        Returns: (passed_bool, new_score, old_score)
        """
        # Get Scores
        new_score = self._score(new_model, X_test, y_test, metric_name)
        
        if old_model is None:
            self.logger.info(
                "No old model to compare. New model wins default. Score: %s",
                new_score,
            )
            return True, new_score, 0.0

        # Try to score old model - may fail if features changed
        try:
            old_score = self._score(old_model, X_test, y_test, metric_name)
        except Exception as e:
            err_msg = str(e).lower()
            compatibility_keywords = (
                "feature",
                "shape",
                "mismatch",
                "expected",
                "input",
            )
            if any(x in err_msg for x in compatibility_keywords):
                self.logger.warning(
                    "Old model incompatible with new data (features/shape mismatch). "
                    "Promoting new model. Error: %s",
                    e,
                )
                return True, new_score, 0.0
            raise  # Re-raise if it's a different error
        
        # Comparison logic
        improvement = new_score - old_score
        
        self.logger.info(
            "Comparison: New=%0.4f, Old=%0.4f, Diff=%0.4f",
            new_score,
            old_score,
            improvement,
        )
        
        # Improvement Threshold (e.g., must be at least equal or better)
        if new_score >= old_score:
            return True, new_score, old_score
        else:
            return False, new_score, old_score

    def _score(self, model, X, y, metric_name):
        preds = model.predict(X)
        
        # Normalize metric name to handle aliases (f1 vs f1_score)
        metric = metric_name.lower().strip()
        
        if metric in ["f1", "f1_score"]:
            # Handle binary vs multiclass automatically
            try:
                return f1_score(y, preds)
            except ValueError:
                return f1_score(y, preds, average='weighted')
                
        elif metric in ["r2", "r2_score"]:
            return r2_score(y, preds)
            
        elif metric in ["accuracy", "acc"]:
            return accuracy_score(y, preds)
            
        elif metric in ["mae", "mean_absolute_error"]:
            return mean_absolute_error(y, preds)
            
        elif metric in ["rmse", "mean_squared_error"]:
            return mean_squared_error(y, preds, squared=False)
            
        else:
            raise ValueError(f"Unknown metric: {metric_name}")