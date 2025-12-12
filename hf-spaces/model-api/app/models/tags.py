from ..schemas import TagInput
from .base import BaseModelWrapper


class TagRecommender(BaseModelWrapper):
    def _init_mock_model(self):
        self.rules = {
            frozenset(["minecraft", "speedrun"]): ["dream", "manhunt"],
            frozenset(["python", "tutorial"]): ["fastapi", "machine learning"],
            frozenset(["vlog"]): ["lifestyle", "day in the life"]
        }

    def predict(self, input_data: TagInput):
        current = set(s.lower() for s in input_data.current_tags)
        recommendations = set()
        
        for key, value in self.rules.items():
            if key.issubset(current):
                recommendations.update(value)
        
        if not recommendations:
            recommendations.update(["viral", "trending", "4k"])
            
        return list(recommendations)