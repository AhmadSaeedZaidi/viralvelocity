from unittest.mock import patch

import pandas as pd
import pytest

from training.pipelines.clickbait_pipeline import (
    feature_engineering_task,
    train_and_tune_task,
    validate_model_task,
)


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        'video_id': ['v1', 'v2'],
        'title': ['Clickbait Title', 'Normal Title'],
        'views': [20000, 500],
        'likes': [10, 50], # Low engagement for v1, High for v2
        'comments': [1, 10],
        'duration_seconds': [60, 120]
    })

@patch("training.pipelines.clickbait_pipeline.get_run_logger")
@patch("training.pipelines.clickbait_pipeline.PIPELINE_CONFIG", {
    "labeling": {"engagement_threshold": 0.05, "min_views": 1000},
    "target": "is_clickbait"
})
def test_feature_engineering_task(mock_logger, sample_df):
    # v1: views=20000 (>1000), likes=10. Ratio ~ 0.0005 (<0.05).
    # Should be clickbait (1).
    # v2: views=500 (<1000). Should be 0.
    
    df = feature_engineering_task.fn(sample_df)
    
    assert 'is_clickbait' in df.columns
    assert 'like_view_ratio' in df.columns
    
    # Check labels
    # Note: The order might be preserved or not depending on implementation,
    # usually pandas preserves index
    assert df.iloc[0]['is_clickbait'] == 1
    assert df.iloc[1]['is_clickbait'] == 0

@patch("training.pipelines.clickbait_pipeline.get_run_logger")
@patch("training.pipelines.clickbait_pipeline.RandomizedSearchCV")
@patch("training.pipelines.clickbait_pipeline.PIPELINE_CONFIG", {
    "target": "is_clickbait",
    "test_size": 0.2,
    "random_state": 42,
    "tuning": {"params": {}, "n_iter": 1, "cv": 2}
})
def test_train_and_tune_task(MockSearch, mock_logger):
    df = pd.DataFrame({
        'f1': [1, 2, 3, 4, 5],
        'f2': [1, 2, 3, 4, 5],
        'is_clickbait': [0, 1, 0, 1, 0]
    })
    
    mock_search_instance = MockSearch.return_value
    mock_search_instance.best_estimator_ = "BestModel"
    mock_search_instance.best_params_ = {}
    
    model, Xt, Xv, yt, yv = train_and_tune_task.fn(df)
    
    assert model == "BestModel"
    MockSearch.assert_called_once()
    mock_search_instance.fit.assert_called_once()

@patch("training.pipelines.clickbait_pipeline.ModelValidator")
@patch("training.pipelines.clickbait_pipeline.PIPELINE_CONFIG", {"metric": "f1_score"})
@patch(
    "training.pipelines.clickbait_pipeline.CONFIG",
    {"global": {"hf_repo_id": "test/repo"}}
)
def test_validate_model_task(MockValidator):
    mock_val_instance = MockValidator.return_value
    mock_val_instance.compare_models.return_value = (True, 0.9, 0.8)
    
    is_better, new, old = validate_model_task.fn("new_model", "X_test", "y_test")
    
    assert is_better is True
    assert new == 0.9
    mock_val_instance.load_production_model.assert_called_with("clickbait/model.pkl")
