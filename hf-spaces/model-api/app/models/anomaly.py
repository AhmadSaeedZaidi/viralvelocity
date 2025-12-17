import numpy as np
from sklearn.ensemble import IsolationForest

from ..schemas import AnomalyInput
from .base import BaseModelWrapper


class AnomalyDetector(BaseModelWrapper):
    def _init_mock_model(self):
        self.model = IsolationForest(contamination=0.1)
        # Mock training with 3 features to match real model
        X = np.random.rand(20, 3)
        self.model.fit(X)

    def predict(self, input_data: AnomalyInput):
        # Features must match training/feature_engineering/base_features.py:
        # prepare_anomaly_features
        # Features: ["log_views", "like_view_ratio", "comment_view_ratio"]

        log_views = np.log1p(input_data.view_count)

        # Avoid division by zero
        safe_views = max(1, input_data.view_count)
        like_view_ratio = input_data.like_count / safe_views
        comment_view_ratio = input_data.comment_count / safe_views

        features = np.array(
            [
                [
                    log_views,
                    like_view_ratio,
                    comment_view_ratio,
                ]
            ]
        )

        pred = self.model.predict(features)[0]
        score = self.model.decision_function(features)[0]

        is_anomaly = True if pred == -1 else False
        return is_anomaly, float(score)

    def get_feature_importance(self) -> dict:
        # Isolation Forest doesn't have standard feature importance
        # But we can return empty or a message
        return {}
