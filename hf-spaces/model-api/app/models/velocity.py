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
            X = np.random.rand(10, 17)
            y = np.random.randint(1000, 50000, 10)
            self.model.fit(X, np.log1p(y))
            self.is_loaded = True
            print(f"DEBUG: Mock model for {self.name} initialized successfully")
        except Exception as e:
            print(f"DEBUG: Failed to init mock model for {self.name}: {e}")
            raise e

    def get_feature_importance(self) -> dict:
        feature_names = [
            "hour_sin", "hour_cos", "publish_day", "is_weekend",
            "log_start_views", "log_duration", "initial_virality_slope",
            "interaction_density", "like_view_ratio", "comment_view_ratio",
            "video_age_hours", "title_len", "caps_ratio", "exclamation_count",
            "question_count", "has_digits", "category_id"
        ]
        try:
            # XGBoost stores importances
            importances = self.model.feature_importances_
            return dict(zip(feature_names, [float(i) for i in importances]))
        except Exception:
            return {}

    def predict(self, input_data: VelocityInput):
        try:
            # Construct feature array in the exact order expected by the model
            # Order from pipeline:
            # hour_sin, hour_cos, publish_day, is_weekend, log_start_views, log_duration,
            # initial_virality_slope, interaction_density, like_view_ratio,
            #  comment_view_ratio, video_age_hours, title_len, caps_ratio,
            # exclamation_count, question_count, has_digits, category_id
            
            features = np.array(
                [
                    [
                        input_data.hour_sin,
                        input_data.hour_cos,
                        input_data.publish_day,
                        input_data.is_weekend,
                        input_data.log_start_views,
                        input_data.log_duration,
                        input_data.initial_virality_slope,
                        input_data.interaction_density,
                        input_data.like_view_ratio,
                        input_data.comment_view_ratio,
                        input_data.video_age_hours,
                        input_data.title_len,
                        input_data.caps_ratio,
                        input_data.exclamation_count,
                        input_data.question_count,
                        input_data.has_digits,
                        input_data.category_id,
                    ]
                ]
            )

            # Prediction is in log space (log1p)
            pred_log = self.model.predict(features)[0]

            # Convert back to real space
            pred_real = np.expm1(pred_log)
            return max(0, int(pred_real))
        except Exception as e:
            print(f"Error in VelocityPredictor: {e}")
            # Return a safe fallback instead of crashing
            return 0
