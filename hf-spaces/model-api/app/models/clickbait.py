import numpy as np
from sklearn.ensemble import RandomForestClassifier

from ..schemas import ClickbaitInput
from ..utils.validators import check_model_compatibility
from .base import BaseModelWrapper


class ClickbaitDetector(BaseModelWrapper):
    EXPECTED_FEATURES = 9

    def _init_mock_model(self):
        self.model = RandomForestClassifier(n_estimators=10)
        X = np.random.rand(25, self.EXPECTED_FEATURES)
        y = [0, 1] * 12 + [0]  # 25 labels to match 25 samples
        self.model.fit(X, y)

    def predict(self, input_data: ClickbaitInput):
        """Predict clickbait using the same 9-feature schema as training.

        Training intentionally excludes engagement metrics to avoid target leakage.
        Feature order (must match training):
        [title_len, caps_ratio, exclamation_count, question_count, has_digits,
         hour_sin, hour_cos, publish_day, is_weekend]
        """

        title = (input_data.title or "").strip()
        title_len = len(title)
        caps_count = sum(1 for c in title if c.isupper())
        caps_ratio = caps_count / (title_len + 1)
        exclamation_count = title.count("!")
        question_count = title.count("?")
        has_digits = int(any(c.isdigit() for c in title))

        hour = int(getattr(input_data, "publish_hour", 0) or 0)
        hour = max(0, min(23, hour))
        hour_sin = float(np.sin(2 * np.pi * hour / 24))
        hour_cos = float(np.cos(2 * np.pi * hour / 24))

        publish_day = int(getattr(input_data, "publish_day", 0) or 0)
        # Training uses pandas weekday (Mon=0..Sun=6); clamp for safety.
        publish_day = max(0, min(6, publish_day))
        is_weekend = int(getattr(input_data, "is_weekend", 0) or 0)
        is_weekend = 1 if is_weekend else 0

        features = np.array(
            [
                [
                    title_len,
                    caps_ratio,
                    exclamation_count,
                    question_count,
                    has_digits,
                    hour_sin,
                    hour_cos,
                    publish_day,
                    is_weekend,
                ]
            ],
            dtype=float,
        )
        check_model_compatibility(
            "clickbait",
            input_features=features.shape[1],
            expected_features=self.EXPECTED_FEATURES,
        )
        pred = self.model.predict(features)[0]
        prob = self.model.predict_proba(features)[0][1]
        return int(pred), float(prob)