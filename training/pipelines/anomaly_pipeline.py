import joblib
import numpy as np
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity
from prefect import flow, get_run_logger, task
from sklearn.ensemble import IsolationForest

# --- Modular Imports ---
from training.feature_engineering import base_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            full_config = yaml.safe_load(f)
        return (
            full_config.get("models", {}).get("anomaly", {}),
            full_config.get("global", {}),
        )
    except FileNotFoundError:
        return {}, {}

ANOMALY_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---

@task(retries=3, name="Load Stats")
def load_data():
    logger = get_run_logger()
    loader = DataLoader()
    
    # Use deduplicated stats directly from loader
    df = loader.get_deduplicated_stats()
    
    logger.info(f"Loaded {len(df)} unique records for anomaly detection.")
    return df

@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Use modular feature preparation
    df_features = base_features.prepare_anomaly_features(df)
    
    # Log correlation matrix for debugging
    corr_matrix = df_features.corr()
    logger.info(f"Feature Correlation Matrix:\n{corr_matrix}")
    
    return df_features

@task(name="Deepchecks: Integrity")
def check_integrity(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Deepchecks works best when it knows it's unsupervised (no label)
    ds = Dataset(df, cat_features=[])
    
    # We use a custom suite or simple integrity check
    suite = data_integrity()
    result = suite.run(ds)
    
    path = "anomaly_integrity.html"
    result.save_as_html(path)
    
    # Log failure but don't crash pipeline (integrity issues are warnings here)
    if not result.passed():
        logger.warning("Integrity checks flagged issues. See report.")
            
    return path, result.passed()

@task(name="Train Isolation Forest")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Configurable parameters
    params = ANOMALY_CONFIG.get("params", {})
    contamination = params.get("contamination", 0.01) # Default 1%
    
    logger.info(f"Training Isolation Forest with contamination={contamination}")
    
    model = IsolationForest(
        n_estimators=200,    # Increased for stability
        contamination=contamination,
        max_samples='auto',
        random_state=42,
        n_jobs=-1
    )
    
    # Fit model
    model.fit(df)
    
    # Calculate anomaly scores and predictions
    scores = model.decision_function(df)
    preds = model.predict(df) # -1 for anomaly, 1 for normal
    
    n_anomalies = (preds == -1).sum()
    detected_rate = n_anomalies / len(df)
    
    # Score Stats
    mean_score = np.mean(scores)
    min_score = np.min(scores) # Most anomalous value
    
    metrics = {
        "n_anomalies": int(n_anomalies),
        "detected_rate": round(detected_rate, 4),
        "avg_normality_score": round(mean_score, 4),
        "most_anomalous_score": round(min_score, 4)
    }
    
    logger.info(f"Training Metrics: {metrics}")
    
    return model, metrics

@task(name="Validate Model Logic")
def validate_model_logic(metrics: dict):
    logger = get_run_logger()
    
    rate = metrics["detected_rate"]
    
    # Sanity bounds
    min_rate = ANOMALY_CONFIG.get("validation", {}).get("min_rate", 0.001)
    max_rate = ANOMALY_CONFIG.get("validation", {}).get("max_rate", 0.10)
    
    if not (min_rate <= rate <= max_rate):
        logger.error(f"Anomaly rate {rate:.2%} out of bounds ({min_rate:.1%} - {max_rate:.1%})")
        return False
        
    logger.info("Model logic validation passed.")
    return True

@task(name="Validate & Upload")
def validate_and_upload(model, integrity_report, is_valid):
    logger = get_run_logger()
    
    if not is_valid:
        logger.warning("Model validation failed. Skipping upload.")
        return "DISCARDED"
    
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if not repo_id:
        logger.info("No HF Repo ID. Saving locally only.")
        joblib.dump(model, "anomaly_model.pkl")
        return "SAVED_LOCAL"
    
    try:
        uploader = ModelUploader(repo_id)
        
        # Save and upload model
        joblib.dump(model, "anomaly_model.pkl")
        uploader.upload_file("anomaly_model.pkl", "anomaly/model.pkl")
        
        # Upload report
        if integrity_report:
            uploader.upload_file(integrity_report, "reports/anomaly_integrity_latest.html")
            
        return "PROMOTED"
        
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return "ERROR_UPLOAD"

@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Pipeline Status: {status}"
    if error:
        msg += f"\nError: {error}"
    
    send_discord_alert(status, "Anomaly Detector", msg, metrics)

@flow(name="Train Anomaly Detector", log_prints=True)
def anomaly_training_flow():
    run_metrics = {}
    try:
        # 1. Load & Clean
        raw_df = load_data()
        run_metrics["Records"] = len(raw_df)
        
        # 2. Featurize (Fixes Correlation)
        processed_df = prepare_features(raw_df)
        run_metrics["Features"] = processed_df.shape[1]
        
        # 3. Integrity Check
        integrity_path, integrity_passed = check_integrity(processed_df)
        
        # 4. Train (Fixes Overfitting/Metrics)
        model, train_metrics = train_model(processed_df)
        run_metrics.update(train_metrics)
        
        # 5. Logic Validation
        is_valid = validate_model_logic(train_metrics)
        
        # 6. Upload
        status = validate_and_upload(model, integrity_path, is_valid)
        run_metrics["Deployment"] = status
        
        notify("SUCCESS", metrics=run_metrics)
        
    except Exception as e:
        notify("FAILURE", error=str(e), metrics=run_metrics)
        raise e

if __name__ == "__main__":
    anomaly_training_flow()