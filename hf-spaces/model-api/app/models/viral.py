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
        avg_rank = sum(input_data.discovery_rank_history) / len(
            input_data.discovery_rank_history
        )
        features = np.array([[avg_rank, input_data.rank_velocity]])
        
        pred = self.model.predict(features)[0]
        prob = self.model.predict_proba(features)[0][1]
        return int(pred), float(prob)