from unittest.mock import patch

import pandas as pd
import pytest

from training.pipelines.clickbait_pipeline import (
    prepare_features,
    train_model,
    validate_and_upload,
)


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {
            "video_id": ["v1", "v2"],
            "title": ["Clickbait Title", "Normal Title"],
            "views": [20000, 500],
            "likes": [10, 50],  # Low engagement for v1, High for v2
            "comments": [1, 10],
            "duration_seconds": [60, 120],
            "published_at": ["2023-01-01", "2023-01-02"],
        }
    )


@patch("training.pipelines.clickbait_pipeline.get_run_logger")
@patch(
    "training.pipelines.clickbait_pipeline.PIPELINE_CONFIG",
    {
        "labeling": {"engagement_threshold": 0.05, "min_views": 1000},
        "target": "is_clickbait",
    },
)
def test_prepare_features(mock_logger, sample_df):
    # v1: views=20000 (>1000), likes=10. Ratio ~ 0.0005 (<0.05).
    # Should be clickbait (1).
    # v2: views=500 (<1000). Should be 0.

    df = prepare_features.fn(sample_df)

    assert "is_clickbait" in df.columns
    assert "like_view_ratio" in df.columns

    # Check labels
    assert df.iloc[0]["is_clickbait"] == 1
    assert df.iloc[1]["is_clickbait"] == 0


@patch("training.pipelines.clickbait_pipeline.get_run_logger")
@patch("training.pipelines.clickbait_pipeline.RandomizedSearchCV")
@patch(
    "training.pipelines.clickbait_pipeline.PIPELINE_CONFIG",
    {
        "target": "is_clickbait",
        "test_size": 0.2,
        "random_state": 42,
        "tuning": {"params": {}, "n_iter": 1, "cv": 2},
    },
)
def test_train_model(MockSearch, mock_logger):
    df = pd.DataFrame(
        {"f1": [1, 2, 3, 4, 5], "f2": [1, 2, 3, 4, 5], "is_clickbait": [0, 1, 0, 1, 0]}
    )

    mock_search_instance = MockSearch.return_value
    mock_search_instance.best_estimator_ = "BestModel"
    mock_search_instance.best_params_ = {}

    model, Xt, Xv, yt, yv, metrics = train_model.fn(df)

    assert model == "BestModel"
    MockSearch.assert_called_once()
    mock_search_instance.fit.assert_called_once()


@patch("training.pipelines.clickbait_pipeline.get_run_logger")
@patch("training.pipelines.clickbait_pipeline.ModelValidator")
@patch("training.pipelines.clickbait_pipeline.PIPELINE_CONFIG", {"metric": "f1_score"})
@patch(
    "training.pipelines.clickbait_pipeline.CONFIG",
    {"global": {"hf_repo_id": "test/repo"}},
)
@patch("training.pipelines.clickbait_pipeline.ModelUploader")
@patch("joblib.dump")
def test_validate_and_upload(mock_dump, MockUploader, MockValidator, mock_logger):
    mock_val_instance = MockValidator.return_value
    mock_val_instance.validate_supervised.return_value = (True, 0.9, 0.8)
    mock_val_instance.load_production_model.return_value = "OldModel"

    status = validate_and_upload.fn("new_model", "X_test", "y_test", {})

    assert status == "PROMOTED"
    mock_val_instance.load_production_model.assert_called_with("clickbait/model.pkl")
    MockUploader.return_value.upload_file.assert_called()
