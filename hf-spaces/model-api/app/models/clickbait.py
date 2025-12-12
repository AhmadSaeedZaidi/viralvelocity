import numpy as np
from schemas import ClickbaitInput
from sklearn.ensemble import RandomForestClassifier

from .base import BaseModelWrapper


class ClickbaitDetector(BaseModelWrapper):
    def _init_mock_model(self):
        self.model = RandomForestClassifier(n_estimators=10)
        X = np.random.rand(10, 2)
        y = [0, 1] * 5
        self.model.fit(X, y)

    def predict(self, input_data: ClickbaitInput):
        engagement_score = (
            input_data.like_count + input_data.comment_count * 2
        ) / max(1, input_data.view_count)
        
        # Heuristic override
        threshold = 0.05
        if input_data.view_count > 10000 and engagement_score < threshold:
            return 1, 0.95 
            
        features = np.array([[engagement_score, input_data.view_count]])
        pred = self.model.predict(features)[0]
        prob = self.model.predict_proba(features)[0][1]
        return int(pred), float(prob)