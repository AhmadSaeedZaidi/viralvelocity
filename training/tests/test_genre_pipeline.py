from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from training.pipelines.genre_pipeline import (
    prepare_features_task,
    svd_optimization_task,
    vectorize_task,
)


@pytest.fixture
def sample_metadata():
    return pd.DataFrame({
        'video_id': ['1', '2'],
        'title': ['Minecraft Gameplay', 'Daily Vlog'],
        'tags': ['gaming, minecraft', 'lifestyle, vlog'],
        'category_id': ['Gaming', 'Lifestyle']
    })

@patch("training.pipelines.genre_pipeline.get_run_logger")
def test_prepare_features_task(mock_logger, sample_metadata):
    df = prepare_features_task.fn(sample_metadata)
    
    assert 'text' in df.columns
    assert 'genre' in df.columns
    # Check text combination
    assert 'minecraft' in df.iloc[0]['text']
    assert 'gaming' in df.iloc[0]['text']

@patch("training.pipelines.genre_pipeline.get_run_logger")
def test_vectorize_task(mock_logger):
    df = pd.DataFrame({
        'text': ['gaming video', 'vlog video', 'gaming minecraft'],
        'genre': ['Gaming', 'Vlog', 'Gaming']
    })
    
    X, y, vectorizer, le = vectorize_task.fn(df)
    
    assert X.shape[0] == 3
    assert len(y) == 3
    # Check label encoding
    assert len(set(y)) == 2 # Gaming, Vlog

@patch("training.pipelines.genre_pipeline.get_run_logger")
@patch("training.pipelines.genre_pipeline.GENRE_CONFIG", {"pca_candidates": [2]})
def test_svd_optimization_task(mock_logger):
    # Create a sparse matrix-like object or dense array
    X = np.random.rand(10, 5) # 10 samples, 5 features
    y = np.array([0, 1] * 5)
    
    X_reduced, svd = svd_optimization_task.fn(X, y)
    
    # Should reduce to 2 components as per config
    assert X_reduced.shape[1] == 2
    assert svd.n_components == 2
