import numpy as np
from sklearn.linear_model import LogisticRegression

from ..schemas import ViralInput
from .base import BaseModelWrapper


class ViralTrendPredictor(BaseModelWrapper):
    def _init_mock_model(self):
        self.model = LogisticRegression()
        X = np.random.rand(10, 2)
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
