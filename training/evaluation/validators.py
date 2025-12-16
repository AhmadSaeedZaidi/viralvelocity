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
    """
    Standardized validator for comparing new models against production models.
    Supports:
    - Supervised Learning (Regression/Classification) via metric comparison.
    - Unsupervised Learning (Anomaly Detection) via heuristic checks.
    - Rule-Based Systems (Tags) via metric comparison.
    """
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
            self.logger.warning(
                f"No production model found at {model_filename}. Assuming first run."
            )
            return None

    def validate_supervised(
        self,
        new_model,
        old_model,
        X_test,
        y_test,
        metric_name="f1_score",
        threshold_improvement=0.0
    ):
        """
        Compares a new supervised model against the old one using a specific metric.
        
        Args:
            new_model: The newly trained model.
            old_model: The production model (can be None).
            X_test: Validation features.
            y_test: Validation labels.
            metric_name: Metric to optimize (e.g., 'f1_score', 'rmse').
            threshold_improvement: Minimum improvement required to promote (default 0).
            
        Returns:
            (passed: bool, new_score: float, old_score: float)
        """
        # 1. Score New Model
        new_score = self._calculate_metric(new_model, X_test, y_test, metric_name)
        
        # 2. Handle First Run
        if old_model is None:
            self.logger.info(
                f"First run. New model score ({metric_name}): {new_score:.4f}"
            )
            return True, new_score, 0.0

        # 3. Score Old Model (Handle Compatibility Issues)
        try:
            old_score = self._calculate_metric(old_model, X_test, y_test, metric_name)
        except Exception as e:
            self.logger.warning(
                f"Old model failed on new data (likely schema change). "
                f"Promoting new model. Error: {e}"
            )
            return True, new_score, 0.0

        # 4. Compare
        lower_is_better = metric_name.lower() in [
            "mae", "mean_absolute_error", "rmse", "mean_squared_error"
        ]
        
        if lower_is_better:
            improvement = old_score - new_score
            passed = improvement >= threshold_improvement
        else:
            improvement = new_score - old_score
            passed = improvement >= threshold_improvement
            
        self.logger.info(
            f"Validation ({metric_name}): New={new_score:.4f}, "
            f"Old={old_score:.4f}, Diff={improvement:.4f}"
        )
        
        if not passed:
            self.logger.warning(
                f"Model failed validation. Improvement {improvement:.4f} < "
                f"{threshold_improvement}"
            )
            
        return passed, new_score, old_score

    def validate_unsupervised(self, metrics: dict, bounds: dict):
        """
        Validates unsupervised models (e.g., Anomaly Detection) using heuristic bounds.
        
        Args:
            metrics (dict): Dictionary of calculated metrics 
                            (e.g., {'anomaly_rate': 0.05}).
            bounds (dict): Dictionary of min/max bounds 
                           (e.g., {'anomaly_rate': (0.01, 0.10)}).
            
        Returns:
            bool: True if all metrics are within bounds.
        """
        passed = True
        for metric, (min_val, max_val) in bounds.items():
            val = metrics.get(metric)
            if val is None:
                self.logger.warning(f"Metric {metric} missing from validation metrics.")
                continue
                
            if not (min_val <= val <= max_val):
                self.logger.warning(
                    f"Metric {metric}={val:.4f} out of bounds [{min_val}, {max_val}]"
                )
                passed = False
                
        if passed:
            self.logger.info("Unsupervised validation passed.")
        return passed

    def _calculate_metric(self, model, X, y, metric_name):
        """Internal helper to calculate metrics safely."""
        preds = model.predict(X)
        metric = metric_name.lower().strip()
        
        if metric in ["f1", "f1_score"]:
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