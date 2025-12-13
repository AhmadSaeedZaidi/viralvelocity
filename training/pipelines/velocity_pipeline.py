import pandas as pd
import numpy as np
import joblib
import os
import yaml
import xgboost as xgb
from sklearn.model_selection import train_test_split
from prefect import flow, task, get_run_logger
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation

# --- Modular Imports ---
from training.feature_engineering import temporal_features
from training.evaluation import metrics
from training.evaluation.validators import ModelValidator
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"

def load_config():
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    return full_config.get("models", {}).get("velocity", {}), full_config.get("global", {})

VELOCITY_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---

@task(retries=3, retry_delay_seconds=30, name="Load Video Data")
def load_data():
    logger = get_run_logger()
    loader = DataLoader()
    # We need full history for rolling averages, but for simplicity we fetch metadata + latest stats
    df = loader.get_joined_data()
    if df.empty:
        raise ValueError("Database returned empty dataframe!")
    return df

@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    
    # 1. Date Features
    df = temporal_features.add_date_features(df, date_col='published_at')
    
    # 2. Channel Rolling Average
    df = temporal_features.calculate_velocity_features(df, window=5)
    
    # 3. Target: For this training, we treat current 'views' as the target
    target = VELOCITY_CONFIG.get("target", "views")
    
    features = ['duration_seconds', 'publish_hour', 'publish_day', 'channel_avg_views_recent', 'likes', 'comments']
    
    # Clean up
    final_df = df[features + [target]].fillna(0)
    
    return final_df

@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    target = VELOCITY_CONFIG.get("target", "views")
    ds = Dataset(df, label=target, cat_features=['publish_day', 'publish_hour'])
    
    integ_suite = data_integrity()
    result = integ_suite.run(ds)
    
    report_path = "velocity_integrity_report.html"
    result.save_as_html(report_path)
    
    if not result.passed():
        logger.error("Data Integrity checks failed!")
        return report_path, False
        
    return report_path, True

@task(name="Train XGBoost")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    target = VELOCITY_CONFIG.get("target", "views")
    X = df.drop(columns=[target])
    y = df[target]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Use params from config if available
    params = VELOCITY_CONFIG.get("tuning", {}).get("params", {})
    # Flatten params list to single value for simple training (taking first value)
    simple_params = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}
    
    model = xgb.XGBRegressor(objective='reg:squarederror', **simple_params)
    model.fit(X_train, y_train)
    
    preds = model.predict(X_test)
    
    # Use modular metrics
    eval_metrics = metrics.get_regression_metrics(y_test, preds)
    
    logger.info(f"Model Training Complete. Metrics: {eval_metrics}")
    
    return model, X_train, X_test, y_train, y_test, eval_metrics

@task(name="Deepchecks: Model Eval")
def run_evaluation_checks(model, X_train, X_test, y_train, y_test):
    target = VELOCITY_CONFIG.get("target", "views")
    # Regression evaluation suite
    train_ds = Dataset(pd.concat([X_train, y_train], axis=1), label=target, cat_features=['publish_day', 'publish_hour'])
    test_ds = Dataset(pd.concat([X_test, y_test], axis=1), label=target, cat_features=['publish_day', 'publish_hour'])
    
    eval_suite = model_evaluation()
    result = eval_suite.run(train_dataset=train_ds, test_dataset=test_ds, model=model)
    
    report_path = "velocity_eval_report.html"
    result.save_as_html(report_path)
    
    return report_path

@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports, current_metrics):
    logger = get_run_logger()
    
    # Initialize uploader (will use env vars HF_USERNAME/HF_MODELS)
    try:
        uploader = ModelUploader()
        repo_id = uploader.repo_id
    except ValueError as e:
        logger.warning(f"Skipping upload: {e}")
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("velocity/model.pkl")
    
    # Compare R2 scores
    passed, new_score, old_score = validator.compare_models(
        model, old_model, X_test, y_test, metric_name="r2"
    )
    
    if passed:
        logger.info("New model is better or equal. Uploading...")
        model_path = "velocity_model.pkl"
        joblib.dump(model, model_path)
        
        uploader.upload_file(model_path, "velocity/model.pkl")
        for name, path in reports.items():
            uploader.upload_file(path, f"reports/velocity_{name}_latest.html")
        return "PROMOTED"
    else:
        logger.info("New model did not improve. Discarding.")
        return "DISCARDED"

@task(name="Notify")
def notify(status, error_msg=None, metrics=None):
    send_discord_alert(status, "Velocity Predictor", 
                       f"Pipeline finished. Error: {error_msg}" if error_msg else "Success", 
                       metrics)

# --- Main Flow ---

@flow(name="Train Velocity Predictor", log_prints=True)
def velocity_training_flow():
    logger = get_run_logger()
    run_metrics = {}
    
    try:
        raw_df = load_data()
        run_metrics["Rows"] = len(raw_df)
        
        processed_df = prepare_features(raw_df)
        
        integrity_path, passed = run_integrity_checks(processed_df)
        if not passed:
            raise Exception("Data Integrity Failed")
            
        model, X_train, X_test, y_train, y_test, eval_metrics = train_model(processed_df)
        run_metrics.update(eval_metrics)
        
        eval_path = run_evaluation_checks(model, X_train, X_test, y_train, y_test)
        
        status = validate_and_upload(
            model, X_test, y_test, 
            {"integrity": integrity_path, "eval": eval_path},
            eval_metrics
        )
        
        notify("SUCCESS", metrics=run_metrics)
        
    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        notify("FAILURE", error_msg=str(e), metrics=run_metrics)
        raise e

if __name__ == "__main__":
    velocity_training_flow()