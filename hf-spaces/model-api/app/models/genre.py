from sklearn.decomposition import IncrementalPCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neural_network import MLPClassifier

from ..schemas import GenreInput
from .base import BaseModelWrapper


class GenreClassifier(BaseModelWrapper):
    def _init_mock_model(self):
        self.tfidf = TfidfVectorizer(max_features=5000, stop_words='english')
        self.pca = IncrementalPCA(n_components=50)
        self.mlp = MLPClassifier(hidden_layer_sizes=(50,), max_iter=10)
        
        texts = [
            "gaming minecraft", "tutorial python", "vlog daily", "makeup tutorial"
        ] * 5
        labels = ["Gaming", "Tutorial", "Vlog", "Lifestyle"] * 5
        
        X_sparse = self.tfidf.fit_transform(texts)
        X_dense = self.pca.fit_transform(X_sparse.toarray())
        self.mlp.fit(X_dense, labels)

    def predict(self, input_data: GenreInput):
        text_data = f"{input_data.title} {' '.join(input_data.tags)}"
        
        vec = self.tfidf.transform([text_data])
        reduced = self.pca.transform(vec.toarray())
        
        pred = self.mlp.predict(reduced)[0]
        probs = self.mlp.predict_proba(reduced)[0]
        confidence = float(max(probs))
        
        return pred, confidence