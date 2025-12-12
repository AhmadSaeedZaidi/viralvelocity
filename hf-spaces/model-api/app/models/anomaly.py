import numpy as np
from sklearn.ensemble import IsolationForest

from ..schemas import AnomalyInput
from .base import BaseModelWrapper


class AnomalyDetector(BaseModelWrapper):
    def _init_mock_model(self):
        self.model = IsolationForest(contamination=0.1)
        X = np.random.rand(20, 4)
        self.model.fit(X)

    def predict(self, input_data: AnomalyInput):
        features = np.array([[
            input_data.view_count, 
            input_data.like_count, 
            input_data.comment_count, 
            input_data.duration_seconds
        ]])
        
        pred = self.model.predict(features)[0]
        score = self.model.decision_function(features)[0]
        
        is_anomaly = True if pred == -1 else False
        return is_anomaly, float(score)