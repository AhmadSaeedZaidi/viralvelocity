import joblib
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import RandomizedSearchCV, train_test_split

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator

# --- Modular Imports ---
from training.feature_engineering import viral_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    return (
        full_config.get("models", {}).get("viral", {}),
        full_config.get("global", {}),
    )


VIRAL_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---


@task(retries=3, name="Load Video Stats History")
def load_data():
    logger = get_run_logger()
    loader = DataLoader()
    df = loader.get_viral_training_data()
    
    if df.empty:
        raise ValueError(
            "No training data found. Ensure search_discovery and "
            "video_stats tables have data. Run collector for a few cycles."
        )
    
    # Diagnostic logging
    logger.info(f"Loaded {len(df)} total stat rows")
    logger.info(f"Unique videos: {df['video_id'].nunique()}")
    
    # Count videos with multiple stat snapshots (needed for velocity calculation)
    stat_counts = df.groupby('video_id').size()
    videos_with_multiple = (stat_counts >= 2).sum()
    logger.info(
        f"Videos with 2+ stat snapshots (usable for training): {videos_with_multiple}"
    )
    
    if videos_with_multiple == 0:
        raise ValueError(
            f"Found {len(df)} rows, but NO videos have 2+ stat snapshots. "
            "The viral model needs repeated observations to calculate view velocity. "
            "Wait for more data collection cycles."
        )
    
    return df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Use modular feature engineering
    try:
        final_df = viral_features.prepare_viral_features(df)
    except ValueError as e:
        # Re-raise with clear message for Prefect
        raise ValueError(f"Feature Engineering Failed: {e}")

    total_videos = df["video_id"].nunique()
    logger.info(
        f"Feature engineering: {total_videos} unique videos, "
        f"kept {len(final_df)} samples."
    )
    
    # Stats
    viral_count = final_df["is_viral"].sum()
    logger.info(f"Viral: {viral_count} ({100 * viral_count / len(final_df):.1f}%)")
    
    return final_df


@task(name="Deepchecks: Integrity")
def run_integrity(df: pd.DataFrame):
    logger = get_run_logger()
    ds = Dataset(df, label="is_viral", cat_features=[])
    integ = data_integrity()
    res = integ.run(ds)
    path = "viral_integrity.html"
    res.save_as_html(path)

    # Always upload report if repo_id exists
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            if res.passed():
                 uploader.upload_reports({"integrity": path}, folder="viral/reports")
            else:
                 logger.warning("Integrity checks failed.")
                 failed_path = path.replace(".html", "_FAILED.html")
                 import os
                 os.rename(path, failed_path)
                 uploader.upload_reports(
                     {"integrity": failed_path}, folder="viral/reports"
                 )
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")
            
    return path, res.passed()


@task(name="Train Logistic Regression")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Strictly define features to avoid leakage
    feature_cols = [
        "like_velocity", "comment_velocity",
        "log_start_views", "start_views", # Include both? Linear model might prefer log
        "like_ratio", "comment_ratio",
        "video_age_hours", "duration_seconds",
        "hours_tracked", "snapshots",
        # New Features
        "initial_virality_slope", "interaction_density",
        "hour_sin", "hour_cos",
        "title_len", "caps_ratio", "has_digits"
    ]
    
    # Ensure columns exist
    feature_cols = [c for c in feature_cols if c in df.columns]
    
    X = df[feature_cols].fillna(0)
    y = df["is_viral"]
    
    logger.info(f"Training with {len(feature_cols)} features: {feature_cols}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    
    # Tuning Config
    tuning_conf = VIRAL_CONFIG.get("tuning", {})
    
    if tuning_conf:
        base_model = LogisticRegression(max_iter=1000, class_weight="balanced")
        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=tuning_conf.get("params", {}),
            n_iter=tuning_conf.get("n_iter", 10),
            cv=tuning_conf.get("cv", 3),
            scoring='f1',
            n_jobs=-1,
            verbose=1
        )
        search.fit(X_train, y_train)
        model = search.best_estimator_
        logger.info(f"Best hyperparameters: {search.best_params_}")
    else:
        model = LogisticRegression(class_weight="balanced", max_iter=1000)
        model.fit(X_train, y_train)
    
    preds = model.predict(X_test)
    
    # Classification Metrics
    eval_metrics = metrics.get_classification_metrics(y_test, preds)
    report = classification_report(y_test, preds)
    logger.info(f"Classification Report:\n{report}")
    logger.info(f"Training Metrics: {eval_metrics}")

    return model, X_train, X_test, y_train, y_test, eval_metrics


@task(name="Deepchecks: Eval")
def run_eval(model, X_train, X_test, y_train, y_test):
    train_ds = Dataset(pd.concat([X_train, y_train], axis=1), label="is_viral")
    test_ds = Dataset(pd.concat([X_test, y_test], axis=1), label="is_viral")

    suite = model_evaluation()
    res = suite.run(train_dataset=train_ds, test_dataset=test_ds, model=model)
    path = "viral_eval.html"
    res.save_as_html(path)
    
    # Always upload report
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            uploader.upload_reports({"eval": path}, folder="viral/reports")
        except Exception:
            pass
            
    return path


@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports):
    logger = get_run_logger()
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if not repo_id:
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("viral/model.pkl")
    metric_name = VIRAL_CONFIG.get("metric", "f1_score")

    passed, new_score, old_score = validator.validate_supervised(
        model, old_model, X_test, y_test, metric_name=metric_name
    )

    if passed:
        logger.info(f"Promoting model. New {metric_name}: {new_score:.4f}")
        joblib.dump(model, "viral_model.pkl")
        uploader = ModelUploader(repo_id)
        uploader.upload_file("viral_model.pkl", "viral/model.pkl")
        uploader.upload_reports(reports, folder="viral/reports")
        return "PROMOTED"
    
    return "DISCARDED"


@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Finished. {error if error else ''}"
    send_discord_alert(status, "Viral Predictor", msg, metrics)


@flow(name="Train Viral Predictor", log_prints=True)
def viral_training_flow():
    logger = get_run_logger()
    run_metrics = {}
    try:
        # 1. Load Data
        raw_df = load_data()
        run_metrics["Raw_Rows"] = len(raw_df)
        
        # 2. Feature Engineering
        df = prepare_features(raw_df)
        run_metrics["Training_Samples"] = len(df)
        run_metrics["Features"] = len(df.columns) - 1
        
        # 3. Integrity Checks
        integrity_path, passed = run_integrity(df)
        if not passed:
            logger.warning("Data Integrity Failed. Continuing pipeline...")
            
        # 4. Train Model
        best_model, Xt, Xv, yt, yv, eval_metrics = train_model(df)
        run_metrics.update(eval_metrics)

        # 5. Evaluate
        eval_path = run_eval(best_model, Xt, Xv, yt, yv)

        # 6. Validate & Upload
        status = validate_and_upload(
            best_model, Xv, yv, {"integrity": integrity_path, "eval": eval_path}
        )
        
        run_metrics["Deployment"] = status
        notify("SUCCESS", metrics=run_metrics)
            
    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        notify("FAILURE", error=str(e), metrics=run_metrics)
        raise e


if __name__ == "__main__":
    viral_training_flow()