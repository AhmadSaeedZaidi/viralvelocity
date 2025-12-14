
import joblib
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
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    models_cfg = full_config.get("models", {})
    anomaly_cfg = models_cfg.get("anomaly", {})
    global_cfg = full_config.get("global", {})
    return anomaly_cfg, global_cfg

ANOMALY_CONFIG, GLOBAL_CONFIG = load_config()

@task(retries=3, name="Load Stats")
def load_data():
    loader = DataLoader()
    df = loader.get_latest_stats()
    return df

@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    df = base_features.clean_dataframe(df)
    df = base_features.calculate_engagement_ratios(df)
    features = ['views', 'likes', 'comments', 'like_view_ratio', 'comment_view_ratio']
    return df[features].fillna(0)

@task(name="Deepchecks: Integrity")
def check_integrity(df: pd.DataFrame):
    ds = Dataset(df, cat_features=[])
    suite = data_integrity()
    result = suite.run(ds)
    path = "anomaly_integrity.html"
    result.save_as_html(path)
    return path, result.passed()

@task(name="Train Isolation Forest")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Configurable contamination
    params = ANOMALY_CONFIG.get("params", {})
    contamination = params.get("contamination", 0.01)
    
    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=42,
    )
    model.fit(df)
    
    # Calc anomaly rate on training set just for logging
    preds = model.predict(df)
    n_anomalies = (preds == -1).sum()
    rate = n_anomalies / len(df)
    
    logger.info(f"Detected {n_anomalies} anomalies ({rate:.2%}) in training batch.")
    
    return model, rate

@task(name="Validate & Upload")
def validate_and_upload(model, integrity_report):
    logger = get_run_logger()
    
    # Initialize uploader\
    try:
        uploader = ModelUploader()
    except ValueError as e:
        logger.warning(f"Skipping upload: {e}")
        return "SKIPPED"
    
    local_path = "anomaly_model.pkl"
    joblib.dump(model, local_path)
    
    uploader.upload_file(local_path, "anomaly/model.pkl")
    uploader.upload_file(integrity_report, "reports/anomaly_integrity_latest.html")
    
    return "PROMOTED"

@task(name="Notify")
def notify(status, error=None, metrics=None):
    send_discord_alert(status, "Anomaly Detector", 
                       f"Finished. {error if error else ''}", metrics)

@flow(name="Train Anomaly Detector", log_prints=True)
def anomaly_training_flow():
    metrics = {}
    try:
        raw = load_data()
        metrics["Records"] = len(raw)
        
        df = prepare_features(raw)
        
        path, passed = check_integrity(df)
        if not passed:
            raise Exception("Integrity Failed")

        model, rate = train_model(df)
        metrics["Anomaly Rate"] = f"{rate:.2%}"
        
        validate_and_upload(model, path)
        notify("SUCCESS", metrics=metrics)
        
    except Exception as e:
        notify("FAILURE", error=str(e), metrics=metrics)
        raise e

if __name__ == "__main__":
    anomaly_training_flow()