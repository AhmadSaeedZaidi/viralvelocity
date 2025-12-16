from unittest.mock import patch

import pandas as pd
import pytest

from training.pipelines.genre_pipeline import (
    prepare_features,
    train_model,
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
def test_prepare_features(mock_logger, sample_metadata):
    df = prepare_features.fn(sample_metadata)
    
    assert 'text' in df.columns
    assert 'genre' in df.columns
    # Check text combination
    assert 'minecraft' in df.iloc[0]['text']
    assert 'gaming' in df.iloc[0]['text']

@patch("training.pipelines.genre_pipeline.get_run_logger")
@patch("training.pipelines.genre_pipeline.GENRE_CONFIG", {"pca_candidates": [2]})
def test_train_model(mock_logger):
    # Create a dataframe with text and genre
    df = pd.DataFrame({
        'text': ['gaming video ' * 10, 'vlog video ' * 10] * 50,
        'genre': ['Gaming', 'Vlog'] * 50
    })
    
    artifacts, Xt, Xv, yt, yv, metrics = train_model.fn(df)
    
    assert "model" in artifacts
    assert "vectorizer" in artifacts
    assert "svd" in artifacts
    assert "le" in artifacts
    
    # Check shapes
    assert Xt.shape[1] == 2 # Reduced to 2 components
    assert len(yt) == Xt.shape[0]
