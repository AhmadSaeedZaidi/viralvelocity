from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from training.pipelines.anomaly_pipeline import (
    load_data,
    prepare_features,
    train_model,
    validate_and_upload,
)


@pytest.fixture
def sample_stats_df():
    return pd.DataFrame({
        'video_id': ['v1', 'v2', 'v3'],
        'views': [100, 200, 10000],
        'likes': [10, 20, 50],
        'comments': [1, 2, 5]
    })

@patch("training.pipelines.anomaly_pipeline.DataLoader")
def test_load_data(MockDataLoader, sample_stats_df):
    mock_loader = MockDataLoader.return_value
    mock_loader.get_latest_stats.return_value = sample_stats_df
    
    df = load_data.fn()
    assert len(df) == 3
    assert 'views' in df.columns

def test_prepare_features(sample_stats_df):
    # Should calculate ratios and fillna
    df = prepare_features.fn(sample_stats_df)
    
    assert 'like_view_ratio' in df.columns
    assert 'comment_view_ratio' in df.columns
    # Check calculation: 10/100 = 0.1
    assert df.iloc[0]['like_view_ratio'] == 0.1
    # Ensure only numeric features returned
    assert 'video_id' not in df.columns

@patch("training.pipelines.anomaly_pipeline.IsolationForest")
def test_train_model(MockIsoForest):
    df = pd.DataFrame(
        np.random.rand(10, 5),
        columns=['views', 'likes', 'comments', 'r1', 'r2']
    )
    
    mock_model = MockIsoForest.return_value
    # Mock predict to return some -1 (anomalies) and 1 (normal)
    mock_model.predict.return_value = np.array([1, 1, 1, 1, 1, 1, 1, 1, -1, -1])
    
    model, rate = train_model.fn(df)
    
    assert rate == 0.2  # 2 out of 10 are anomalies
    mock_model.fit.assert_called_once()

@patch("training.pipelines.anomaly_pipeline.ModelUploader")
@patch("joblib.dump")
def test_validate_and_upload(mock_dump, MockUploader):
    mock_model = MagicMock()
    status = validate_and_upload.fn(mock_model, "report.html")
    
    assert status == "PROMOTED"
    mock_dump.assert_called_once()
    MockUploader.return_value.upload_file.assert_called()
