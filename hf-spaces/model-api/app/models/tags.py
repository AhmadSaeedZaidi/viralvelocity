from ..schemas import TagInput
from .base import BaseModelWrapper


class TagRecommender(BaseModelWrapper):
    def _init_mock_model(self):
        self.rules = {
            frozenset(["minecraft", "speedrun"]): ["dream", "manhunt"],
            frozenset(["python", "tutorial"]): ["fastapi", "machine learning"],
            frozenset(["vlog"]): ["lifestyle", "day in the life"]
        }
        # Mock mode doesn't use self.model directly in this implementation
        self.model = None 

    def predict(self, input_data: TagInput):
        current = set(s.lower() for s in input_data.current_tags)
        recommendations = set()
        
        if self.model is not None:
            # Real Model (Pandas DataFrame)
            # Columns: antecedents (list), consequents (list), lift, confidence, etc.
            df = self.model
            
            # We look for rules where antecedents are a subset of current tags
            # This can be slow if we have many rules, but for now it's fine.
            # Optimization: Pre-process rules into a more efficient structure on load.
            
            for _, row in df.iterrows():
                antecedents = set(row['antecedents'])
                if antecedents.issubset(current):
                    recommendations.update(row['consequents'])
                    
        else:
            # Mock Model
            for key, value in self.rules.items():
                if key.issubset(current):
                    recommendations.update(value)
        
        if not recommendations:
            recommendations.update(["viral", "trending", "4k"])
            
        return list(recommendations)