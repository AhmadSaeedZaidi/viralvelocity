import numpy as np
import xgboost as xgb

from ..schemas import VelocityInput
from .base import BaseModelWrapper


class VelocityPredictor(BaseModelWrapper):
    def __init__(self, name: str, repo_path: str = "velocity/model.pkl"):
        super().__init__(name, repo_path)

    def _init_mock_model(self):
        print(f"DEBUG: Initializing mock model for {self.name}")
        try:
            self.model = xgb.XGBRegressor(n_estimators=10, max_depth=3)
            X = np.random.rand(10, 5)
            y = np.random.randint(1000, 50000, 10)
            self.model.fit(X, y)
            self.is_loaded = True
            print(f"DEBUG: Mock model for {self.name} initialized successfully")
        except Exception as e:
            print(f"DEBUG: Failed to init mock model for {self.name}: {e}")
            raise e

    def predict(self, input_data: VelocityInput):
        features = np.array(
            [
                [
                    input_data.slope_views,
                    input_data.slope_engagement,
                    input_data.video_stats_24h.duration_seconds,
                    input_data.channel_stats.avg_views_last_5,
                    input_data.video_stats_24h.published_hour,
                ]
            ]
        )

        pred = self.model.predict(features)[0]
        return max(0, int(pred))
