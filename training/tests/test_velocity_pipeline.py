from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from training.pipelines.velocity_pipeline import prepare_features, train_model


@pytest.fixture
def sample_data():
    return pd.DataFrame({
        'video_id': ['v1', 'v2'],
        'published_at': pd.to_datetime(['2023-01-01 10:00:00', '2023-01-02 14:00:00']),
        'channel_id': ['c1', 'c1'],
        'start_views': [100, 200],
        'target_views': [1000, 2000],
        'start_likes': [10, 20],
        'start_comments': [1, 2],
        'duration_seconds': [60, 120]
    })

@patch("training.pipelines.velocity_pipeline.VELOCITY_CONFIG", {"target": "views"})
@patch("training.pipelines.velocity_pipeline.get_run_logger")
def test_prepare_features(mock_logger, sample_data):
    # Test feature engineering logic
    df = prepare_features.fn(sample_data)
    
    assert 'publish_hour' in df.columns
    assert 'publish_day' in df.columns
    assert 'start_views' in df.columns
    assert 'likes' in df.columns
    assert 'comments' in df.columns
    
    # Check values
    assert df.iloc[0]['publish_hour'] == 10
    assert df.iloc[1]['publish_hour'] == 14
    assert df.iloc[0]['likes'] == 10
    assert df.iloc[0]['comments'] == 1

@patch("training.pipelines.velocity_pipeline.xgb.XGBRegressor")
@patch("training.pipelines.velocity_pipeline.VELOCITY_CONFIG", {
    "target": "views",
    "tuning": {"params": {"learning_rate": 0.1}}
})
def test_train_model(MockXGB):
    df = pd.DataFrame({
        'feature1': [1, 2, 3, 4, 5],
        'feature2': [1, 2, 3, 4, 5],
        'views': [10, 20, 30, 40, 50]
    })
    
    mock_model = MockXGB.return_value
    # Mock predictions for test set (size 1)
    mock_model.predict.return_value = np.array([10, 20])
    
    model, Xt, Xv, yt, yv, metrics = train_model.fn(df)
    
    MockXGB.assert_called_once()
    mock_model.fit.assert_called_once()
    # Depending on what get_regression_metrics returns
    assert 'r2' in metrics or 'rmse' in metrics
