import joblib
import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostRegressor
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.model_selection import train_test_split

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator
from training.feature_engineering import base_features, temporal_features, text_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

CONFIG_PATH = "training/config/training_config.yaml"


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            full_config = yaml.safe_load(f)
        return (
            full_config.get("models", {}).get("velocity", {}),
            full_config.get("global", {}),
        )
    except FileNotFoundError:
        return {}, {}


VELOCITY_CONFIG, GLOBAL_CONFIG = load_config()


@task(retries=3, retry_delay_seconds=30, name="Load Video Data")
def load_data():
    logger = get_run_logger()
    logger.info("Loading velocity data")
    loader = DataLoader()
    
    MIN_TRACKING_HOURS = 2
    df = loader.get_velocity_training_data(min_hours=MIN_TRACKING_HOURS)
    
    if df.empty:
        df = loader.get_training_pairs_flexible()
        if df.empty:
            raise ValueError("No training data found.")
    
    # Filter noise
    df = df[df['start_views'] >= 10]
    
    df['target_views'] = df['target_views'].clip(lower=0)
    df['start_views'] = df['start_views'].clip(lower=0)
    
    return df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    logger.info("Preparing features (Strictly No Data Leaks)")
    
    df = temporal_features.add_date_features(df, date_col="published_at")
    
    if "publish_hour" in df.columns:
        df["hour_sin"] = np.sin(2 * np.pi * df["publish_hour"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["publish_hour"] / 24)
    
    df["like_view_ratio"] = df["start_likes"] / (df["start_views"] + 1)
    df["comment_view_ratio"] = df["start_comments"] / (df["start_views"] + 1)
    
    df["published_at"] = pd.to_datetime(df["published_at"])
    df["start_time"] = pd.to_datetime(df["start_time"])
    
    time_delta = (df["start_time"] - df["published_at"]).dt.total_seconds()
    df["video_age_hours"] = (time_delta / 3600.0).clip(lower=0.5)

    # Initial Virality Slope (Log-Log Slope at T=0)
    # Formula: log(current_views) / log(current_age)
    df["initial_virality_slope"] = np.log1p(df["start_views"]) / np.log1p(df["video_age_hours"])
    
    # Interaction Density (Log Space)
    interaction_num = np.log1p(df["start_likes"] + df["start_comments"] * 2)
    interaction_den = np.log1p(df["start_views"] + 1)
    df["interaction_density"] = interaction_num / interaction_den

    if "title" in df.columns:
        try:
            df = text_features.extract_title_features(df, title_col="title")
        except Exception:
                df["title"] = df["title"].fillna("")
                df["title_len"] = df["title"].str.len()
                df["caps_ratio"] = (
                    df["title"].str.count(r"[A-Z]") / (df["title_len"] + 1)
                )
                df["exclamation_count"] = df["title"].str.count("!")
                df["question_count"] = df["title"].str.count("\\?")
                df["has_digits"] = df["title"].str.contains(r"\\d").astype(int)

    df["log_start_views"] = np.log1p(df["start_views"])
    df["log_duration"] = np.log1p(df["duration_seconds"])

    target_col = VELOCITY_CONFIG.get("target", "views")
    df[target_col] = df["target_views"]

    features = [
        "hour_sin", "hour_cos", "publish_day", "is_weekend",
        "log_start_views", "log_duration",
        "initial_virality_slope", "interaction_density", 
        "like_view_ratio", "comment_view_ratio",
        "video_age_hours",
        "title_len", "caps_ratio", "exclamation_count", "question_count", "has_digits",
        "category_id"
    ]
    
    available_features = [f for f in features if f in df.columns]
    
    if "category_id" in df.columns:
        df["category_id"] = df["category_id"].fillna(-1).astype(int)

    final_df = df[available_features + [target_col]]
    final_df = base_features.clean_dataframe(final_df, fill_value=0)

    logger.info(
        "Features prepared: %d cols, %d rows",
        len(available_features),
        len(final_df),
    )
    return final_df


@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    target_col = VELOCITY_CONFIG.get("target", "views")
    
    if len(df) < 50:
        return "skipped_small_dataset.html", True
    
    potential_cat = ["publish_day", "is_weekend", "category_id"]
    cat_features = [f for f in potential_cat if f in df.columns]
    
    ds = Dataset(df, label=target_col, cat_features=cat_features)
    result = data_integrity().run(ds)

    report_path = "velocity_integrity_report.html"
    result.save_as_html(report_path)

    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            if result.passed():
                uploader.upload_file(report_path, "velocity/reports/velocity_integrity_latest.html")
            else:
                logger.warning("Integrity issues found.")
                uploader.upload_file(report_path, "velocity/reports/velocity_integrity_FAILED.html")
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")
            
    return report_path, result.passed()


@task(name="Train CatBoost")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    target_col = VELOCITY_CONFIG.get("target", "views")

    X = df.drop(columns=[target_col])
    y = df[target_col]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    cat_features = []
    if "category_id" in X_train.columns:
        cat_features = ["category_id"]
        X_train["category_id"] = X_train["category_id"].astype(int)
        X_test["category_id"] = X_test["category_id"].astype(int)

    logger.info("Training CatBoost (Manual Log-Transform + Clip)")

    # Manual Log Transform to allow for Safety Clipping (train only)
    y_train_log = np.log1p(y_train)

    model = CatBoostRegressor(
        iterations=2000,
        learning_rate=0.02,
        depth=6,
        l2_leaf_reg=5,
        loss_function='Huber:delta=1.0',
        verbose=0,
        random_seed=42,
        allow_writing_files=False,
        cat_features=cat_features if cat_features else None
    )

    model.fit(X_train, y_train_log)
    
    # 1. Predict in Log Space
    preds_log = model.predict(X_test)
    preds_log = np.clip(preds_log, 0, 25) # Safety Clip

    # 2. Calculate LOG-SPACE Metrics (The "Physicists" View)
    # This measures how well the model learned the exponential curve
    # regardless of the massive scale differences.
    y_test_log = np.log1p(y_test)
    log_metrics = metrics.get_regression_metrics(y_test_log, preds_log)
    # Rename with prefix
    log_metrics = {f"log_{k}": v for k, v in log_metrics.items()}

    # 3. Calculate REAL-SPACE Metrics (The "Business" View)
    preds = np.expm1(preds_log)
    real_metrics = metrics.get_regression_metrics(y_test, preds)
    
    # Merge metrics for reporting
    eval_metrics = {**real_metrics, **log_metrics}
    
    logger.info(f"Metrics (Log & Real): {eval_metrics}")

    return model, X_train, X_test, y_train, y_test, eval_metrics


@task(name="Deepchecks: Model Eval")
def run_evaluation_checks(model, X_train, X_test, y_train, y_test):
    target_col = VELOCITY_CONFIG.get("target", "views")
    potential_cat = ["publish_day", "is_weekend", "category_id"]
    cat_features = [f for f in potential_cat if f in X_train.columns]
    
    # Manual wrapper to allow Deepchecks to call predict() naturally
    class LogModelWrapper:
        def __init__(self, internal_model):
            self.model = internal_model
        def predict(self, X):
            p_log = self.model.predict(X)
            p_log = np.clip(p_log, 0, 25) # Apply safety clip here too
            return np.expm1(p_log)
    
    wrapped_model = LogModelWrapper(model)
    
    # Build Deepchecks datasets
    train_df = pd.concat([X_train, y_train], axis=1)
    test_df = pd.concat([X_test, y_test], axis=1)

    train_ds = Dataset(
        train_df,
        label=target_col,
        cat_features=cat_features,
    )
    test_ds = Dataset(
        test_df,
        label=target_col,
        cat_features=cat_features,
    )

    result = model_evaluation().run(
        train_dataset=train_ds, test_dataset=test_ds, model=wrapped_model
    )
    report_path = "velocity_eval_report.html"
    result.save_as_html(report_path)

    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            uploader.upload_file(report_path, "velocity/reports/velocity_eval_latest.html")
        except Exception as e:
            logger = get_run_logger()
            logger.warning(f"Failed to upload eval report: {e}")

    return report_path


@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports):
    logger = get_run_logger()
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if not repo_id:
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model_raw = validator.load_production_model("velocity/model.pkl")
    metric_name = VELOCITY_CONFIG.get("metric", "r2_score")
    
    # Wrap model for validation comparison
    class LogModelWrapper:
        def __init__(self, internal_model):
            self.model = internal_model
        def predict(self, X):
            p_log = self.model.predict(X)
            p_log = np.clip(p_log, 0, 25)
            return np.expm1(p_log)
    
    wrapped_new = LogModelWrapper(model)
    
    # Wrap OLD Model (Critical Fix: Old model on disk is raw log-space)
    wrapped_old = None
    if old_model_raw is not None:
        wrapped_old = LogModelWrapper(old_model_raw)

    passed, new_score, old_score = validator.compare_models(
        wrapped_new, wrapped_old, X_test, y_test, metric_name=metric_name
    )

    if passed:
        logger.info(f"Promoting model ({new_score:.4f} vs {old_score:.4f})")
        joblib.dump(model, "velocity_model.pkl") 
        uploader = ModelUploader(repo_id)
        uploader.upload_file("velocity_model.pkl", "velocity/model.pkl")
        uploader.upload_reports(reports, folder="velocity/reports")
        return "PROMOTED"
    return "DISCARDED"


@task(name="Notify")
def notify(status, error_msg=None, metrics=None):
    msg = f"Finished. Error: {error_msg}" if error_msg else "Success"
    send_discord_alert(status, "Velocity Predictor", msg, metrics)


@flow(name="Train Velocity Predictor", log_prints=True)
def velocity_training_flow():
    logger = get_run_logger()
    run_metrics = {}
    try:
        raw_df = load_data()
        run_metrics["Rows"] = len(raw_df)

        processed_df = prepare_features(raw_df)
        run_metrics["Features"] = processed_df.shape[1] - 1

        integrity_path, passed = run_integrity_checks(processed_df)
        if not passed:
            logger.warning("Data Integrity Failed. Continuing pipeline...")

        (
            model,
            X_train,
            X_test,
            y_train,
            y_test,
            eval_metrics,
        ) = train_model(processed_df)
        run_metrics.update(eval_metrics)

        eval_path = run_evaluation_checks(model, X_train, X_test, y_train, y_test)
        
        status = validate_and_upload(
            model, X_test, y_test, {"integrity": integrity_path, "eval": eval_path}
        )
        run_metrics["Deployment"] = status
        notify("SUCCESS", metrics=run_metrics)

    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        notify("FAILURE", error_msg=str(e), metrics=run_metrics)
        raise e


if __name__ == "__main__":
    velocity_training_flow()