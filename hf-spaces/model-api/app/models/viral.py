import numpy as np
import pandas as pd
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
        # Construct feature dictionary with correct names
        features_dict = {
            "like_velocity": input_data.like_velocity,
            "comment_velocity": input_data.comment_velocity,
            "log_start_views": input_data.log_start_views,
            "start_views": np.expm1(input_data.log_start_views),
            "like_ratio": input_data.like_ratio,
            "comment_ratio": input_data.comment_ratio,
            "video_age_hours": input_data.video_age_hours,
            "duration_seconds": input_data.duration_seconds,
            "hours_tracked": 2.0,  # Placeholder
            "snapshots": 2,  # Placeholder
            "initial_virality_slope": input_data.initial_virality_slope,
            "interaction_density": input_data.interaction_density,
            "hour_sin": input_data.hour_sin,
            "hour_cos": input_data.hour_cos,
            "title_len": input_data.title_len,
            "caps_ratio": input_data.caps_ratio,
            "has_digits": input_data.has_digits,
        }

        # Create DataFrame to preserve feature names
        features_df = pd.DataFrame([features_dict])

        feature_order = [
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
        features_df = features_df[feature_order]

        pred = self.model.predict(features_df)[0]
        prob = self.model.predict_proba(features_df)[0][1]
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
