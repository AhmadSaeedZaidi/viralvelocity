from unittest.mock import patch

import pandas as pd
import pytest

from training.pipelines.viral_pipeline import prepare_features, train_model


@pytest.fixture
def trending_history():
    # Create a history for a single video that goes viral
    # Rank drops (improves) over time
    data = []
    # Video 1: Starts at 50, goes to 5 (Viral)
    for i in range(5):
        data.append({
            'video_id': 'v1',
            'rank': 50 - (i * 10), # 50, 40, 30, 20, 10
            'discovered_at': pd.Timestamp('2023-01-01 10:00:00') + pd.Timedelta(hours=i)
        })
    # Video 2: Starts at 100, stays 100 (Not Viral)
    for i in range(5):
        data.append({
            'video_id': 'v2',
            'rank': 100,
            'discovered_at': pd.Timestamp('2023-01-01 10:00:00') + pd.Timedelta(hours=i)
        })
        
    return pd.DataFrame(data)

@patch("training.pipelines.viral_pipeline.get_run_logger")
def test_prepare_features(mock_logger, trending_history):
    df = prepare_features.fn(trending_history)
    
    assert len(df) == 2 # 2 videos
    
    v1 = df[df['start_rank'] == 50].iloc[0]
    v2 = df[df['start_rank'] == 100].iloc[0]
    
    # Check Viral Label (Rank <= 10 is viral)
    # v1 ends at 10 -> Viral
    assert v1['is_viral'] == 1
    # v2 ends at 100 -> Not Viral
    assert v2['is_viral'] == 0
    
    # Check Velocity
    # v1: Rank change 10 - 50 = -40. Time diff 4 hours (14400s).
    # Velocity should be negative (rank improving)
    assert v1['velocity'] < 0

@patch("training.pipelines.viral_pipeline.RandomizedSearchCV")
@patch(
    "training.pipelines.viral_pipeline.VIRAL_CONFIG",
    {"tuning": {"params": {}, "n_iter": 1}}
)
def test_train_model(MockSearch):
    df = pd.DataFrame({
        'velocity': [-0.5, -0.1, 0.0, 0.1],
        'start_rank': [50, 20, 100, 90],
        'is_viral': [1, 1, 0, 0]
    })
    
    mock_search = MockSearch.return_value
    mock_search.best_estimator_ = "BestModel"
    
    model, Xt, Xv, yt, yv, metrics = train_model.fn(df)
    
    assert model == "BestModel"
    MockSearch.assert_called()
