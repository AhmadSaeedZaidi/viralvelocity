import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from training.pipelines.genre_pipeline import (
    prepare_features_task,
    vectorize_task,
    svd_optimization_task
)

@pytest.fixture
def sample_metadata():
    return pd.DataFrame({
        'video_id': ['1', '2'],
        'title': ['Minecraft Gameplay', 'Daily Vlog'],
        'tags': ['gaming, minecraft', 'lifestyle, vlog'],
        'category_id': ['Gaming', 'Lifestyle']
    })

def test_prepare_features_task(sample_metadata):
    df = prepare_features_task.fn(sample_metadata)
    
    assert 'text' in df.columns
    assert 'genre' in df.columns
    # Check text combination
    assert 'minecraft' in df.iloc[0]['text']
    assert 'gaming' in df.iloc[0]['text']

def test_vectorize_task():
    df = pd.DataFrame({
        'text': ['gaming video', 'vlog video', 'gaming minecraft'],
        'genre': ['Gaming', 'Vlog', 'Gaming']
    })
    
    X, y, vectorizer, le = vectorize_task.fn(df)
    
    assert X.shape[0] == 3
    assert len(y) == 3
    # Check label encoding
    assert len(set(y)) == 2 # Gaming, Vlog

@patch("training.pipelines.genre_pipeline.GENRE_CONFIG", {"pca_candidates": [2]})
def test_svd_optimization_task():
    # Create a sparse matrix-like object or dense array
    X = np.random.rand(10, 5) # 10 samples, 5 features
    y = np.array([0, 1] * 5)
    
    X_reduced, svd = svd_optimization_task.fn(X, y)
    
    # Should reduce to 2 components as per config
    assert X_reduced.shape[1] == 2
    assert svd.n_components == 2
