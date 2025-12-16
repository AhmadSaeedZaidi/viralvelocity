from unittest.mock import mock_open, patch

import pandas as pd
import pytest

from training.pipelines.tags_pipeline import (
    load_config,
    load_data,
    prepare_features,
    train_model,
    validate_and_upload,
)

# --- Fixtures ---

@pytest.fixture
def sample_df():
    return pd.DataFrame({
        'video_id': ['1', '2', '3'],
        'tags': [
            'python, coding, tutorial',
            'python, machine learning',
            'coding, tutorial'
        ],
        'views': [1000, 2000, 1500]
    })

@pytest.fixture
def sample_dataset():
    return [
        ['python', 'coding', 'tutorial'],
        ['python', 'machine learning'],
        ['coding', 'tutorial']
    ]

@pytest.fixture
def mock_config():
    return {
        "models": {
            "tags": {
                "params": {
                    "min_support": 0.1,
                    "min_threshold": 1.0
                }
            }
        },
        "global": {}
    }

# --- Tests ---

@patch("training.pipelines.tags_pipeline.get_run_logger")
def test_prepare_features(mock_logger, sample_df):
    """Test that tags string is correctly split into lists."""
    result = prepare_features.fn(sample_df)
    
    assert len(result) == 3
    assert result[0] == ['python', 'coding', 'tutorial']
    assert result[1] == ['python', 'machine learning']
    assert 'coding' in result[2]

@patch("training.pipelines.tags_pipeline.get_run_logger")
def test_prepare_features_empty(mock_logger):
    """Test handling of empty or null tags."""
    df = pd.DataFrame({'tags': [None, '']})
    # The function drops na, so None should be skipped.
    # The implementation filters empty strings:
    #   [t.strip().lower() for t in tags.split(',') if t.strip()]
    # So '' splits to [''], but 'if t.strip()' filters it out.
    # Resulting list is empty [].
    
    result = prepare_features.fn(df)
    # Based on implementation: df['tags'].dropna() removes None.
    # ' '.split(',') -> [''] -> filtered -> []
    assert len(result) == 0

@patch("builtins.open", new_callable=mock_open, read_data="data")
@patch("yaml.safe_load")
def test_load_config(mock_yaml, mock_file, mock_config):
    """Test config loading."""
    mock_yaml.return_value = mock_config
    
    tags_config, global_config = load_config()
    
    assert tags_config['params']['min_support'] == 0.1
    assert global_config == {}

@patch("training.pipelines.tags_pipeline.get_run_logger")
@patch("training.pipelines.tags_pipeline.DataLoader")
def test_load_data(MockDataLoader, mock_logger):
    """Test data loading and filtering."""
    # Setup mock
    mock_loader = MockDataLoader.return_value
    df = pd.DataFrame({
        'views': [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        'tags': ['tag'] * 10,
        'title': ['title'] * 10
    })
    mock_loader.get_joined_data.return_value = df
    
    # Run task
    result = load_data.fn()
    
    # Should filter top 10% (quantile 0.90 of 1-100 is roughly 91)
    # In this simple range 10-100, 90th percentile is 91.
    # So only 100 should remain.
    assert len(result) == 1
    assert result.iloc[0]['views'] == 100

@patch("training.pipelines.tags_pipeline.get_run_logger")
@patch(
    "training.pipelines.tags_pipeline.TAGS_CONFIG",
    {"params": {"min_support": 0.01, "min_threshold": 0.01}}
)
def test_train_model(mock_logger, sample_dataset):
    """Test rule generation with mlxtend."""
    # We use very low thresholds to ensure rules are generated from small sample
    rules, metrics = train_model.fn(sample_dataset)
    
    assert not rules.empty
    assert 'antecedents' in rules.columns
    assert 'consequents' in rules.columns
    assert 'lift' in rules.columns
    assert metrics['rule_count'] > 0

@patch("training.pipelines.tags_pipeline.get_run_logger")
@patch("training.pipelines.tags_pipeline.ModelUploader")
@patch("joblib.dump")
def test_validate_and_upload_success(mock_dump, MockUploader, mock_logger):
    """Test successful validation and upload."""
    mock_uploader = MockUploader.return_value
    
    # Create dummy rules dataframe
    rules = pd.DataFrame({'rule': range(15)}) # > 10 rules
    
    status = validate_and_upload.fn(rules, {}, True, "report.html")
    
    assert status == "PROMOTED"
    mock_dump.assert_called_once()
    assert mock_uploader.upload_file.call_count == 1

@patch("training.pipelines.tags_pipeline.get_run_logger")
@patch("training.pipelines.tags_pipeline.ModelUploader")
def test_validate_and_upload_failure(MockUploader, mock_logger):
    """Test validation failure (too few rules)."""
    rules = pd.DataFrame({'rule': range(5)}) # < 10 rules
    
    status = validate_and_upload.fn(rules, {}, False, "report.html")
    
    assert status == "DISCARDED"
    MockUploader.return_value.upload_file.assert_not_called()
