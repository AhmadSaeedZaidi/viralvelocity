import numpy as np
from sklearn.linear_model import LogisticRegression

from ..schemas import ViralInput
from .base import BaseModelWrapper


class ViralTrendPredictor(BaseModelWrapper):
    def _init_mock_model(self):
        self.model = LogisticRegression()
        X = np.random.rand(10, 17)
        y = [0, 1] * 5
        self.model.fit(X, y)

    def predict(self, input_data: ViralInput):
        features = np.array(
            [
                [
                    input_data.like_velocity,
                    input_data.comment_velocity,
                    input_data.log_start_views,
                    # input_data.start_views,
                    # Missing in schema, derived from log_start_views if needed
                    np.expm1(input_data.log_start_views),
                    input_data.like_ratio,
                    input_data.comment_ratio,
                    input_data.video_age_hours,
                    input_data.duration_seconds,
                    2.0,  # hours_tracked placeholder
                    2,  # snapshots placeholder
                    input_data.initial_virality_slope,
                    input_data.interaction_density,
                    input_data.hour_sin,
                    input_data.hour_cos,
                    input_data.title_len,
                    input_data.caps_ratio,
                    input_data.has_digits,
                ]
            ]
        )

        pred = self.model.predict(features)[0]
        prob = self.model.predict_proba(features)[0][1]
        return int(pred), float(prob)

    def get_feature_importance(self) -> dict:
        if not self.is_loaded or self.model is None:
            return {}
        
        feature_names = [
            "like_velocity",
            "comment_velocity",
            "log_start_views",
            "start_views",
            "like_ratio",
            "comment_ratio",
            "video_age_hours",
            "duration_seconds",
            "hours_tracked",
            "snapshots",
            "initial_virality_slope",
            "interaction_density",
            "hour_sin",
            "hour_cos",
            "title_len",
            "caps_ratio",
            "has_digits",
        ]
        
        try:
            # Logistic Regression uses coefficients
            if hasattr(self.model, "coef_"):
                # coef_ is shape (1, n_features) for binary classification
                importances = np.abs(self.model.coef_[0])
                return dict(zip(feature_names, [float(i) for i in importances]))
        except Exception:
            pass
        return {}
