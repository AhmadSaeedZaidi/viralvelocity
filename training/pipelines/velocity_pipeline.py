import joblib
import pandas as pd
import xgboost as xgb
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.model_selection import RandomizedSearchCV, train_test_split

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator

# --- Modular Imports ---
from training.feature_engineering import base_features, temporal_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    return (
        full_config.get("models", {}).get("velocity", {}),
        full_config.get("global", {}),
    )


VELOCITY_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---


@task(retries=3, retry_delay_seconds=30, name="Load Video Data")
def load_data():
    logger = get_run_logger()
    logger.info("Starting data load for velocity pipeline (using search_discovery)")
    loader = DataLoader()
    
    # Use search_discovery with 2-hour minimum tracking (for early data)
    # This predicts view growth from T=0 to T=latest (typically 2h to 24h range)
    MIN_TRACKING_HOURS = 2
    df = loader.get_velocity_training_data(min_hours=MIN_TRACKING_HOURS)
    
    if df.empty:
        logger.warning("No data from search_discovery. Trying legacy fallback...")
        df = loader.get_training_pairs_flexible()
        
        if df.empty:
            raise ValueError(
                "No training data found. Ensure search_discovery and video_stats "
                "tables have data. The data collector needs to run for a few cycles."
            )
    
    # Diagnostic logging
    logger.info(f"Loaded {len(df)} samples (min {MIN_TRACKING_HOURS}h tracking)")
    target_min, target_max = df['target_views'].min(), df['target_views'].max()
    start_min, start_max = df['start_views'].min(), df['start_views'].max()
    logger.info(f"Target views: {target_min:.0f} - {target_max:.0f}")
    logger.info(f"Start views: {start_min:.0f} - {start_max:.0f}")
    
    if 'hours_tracked' in df.columns:
        h_min, h_max = df['hours_tracked'].min(), df['hours_tracked'].max()
        logger.info(f"Tracking window: {h_min:.1f} - {h_max:.1f} hours")
        logger.info(f"Avg tracking: {df['hours_tracked'].mean():.1f} hours")
    
    return df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    logger.info("Preparing features for velocity model")
    
    # --- 1. Temporal Features (modular) ---
    df = temporal_features.add_date_features(df, date_col="published_at")
    
    # --- 2. Engagement Features (modular) ---
    # Rename columns for base_features compatibility
    df["views"] = df["start_views"]
    df["likes"] = df["start_likes"]
    df["comments"] = df["start_comments"]
    df = base_features.calculate_engagement_ratios(df)
    
    # --- 3. Growth-based Features ---
    hours = df["hours_tracked"] + 0.1  # Avoid div by zero
    
    # View growth rate (views gained per hour)
    df["view_growth_rate"] = (df["target_views"] - df["start_views"]) / hours
    
    # Engagement velocity (likes/comments gained per hour)
    if "end_likes" in df.columns:
        df["like_growth_rate"] = (df["end_likes"] - df["start_likes"]) / hours
        df["comment_growth_rate"] = (
            (df["end_comments"] - df["start_comments"]) / hours
        )
    
    # Video age at first observation (hours since publish)
    df["published_at"] = pd.to_datetime(df["published_at"])
    df["start_time"] = pd.to_datetime(df["start_time"])
    time_delta = (df["start_time"] - df["published_at"]).dt.total_seconds()
    df["video_age_hours"] = (time_delta / 3600.0).clip(lower=0)

    # --- 4. Define Target ---
    target_col = VELOCITY_CONFIG.get("target", "views")
    df[target_col] = df["target_views"]

    # --- 5. Select Final Features ---
    features = [
        # Temporal
        "publish_hour",
        "publish_day",
        "is_weekend",
        # Initial state
        "duration_seconds",
        "start_views",
        "start_likes",
        "start_comments",
        # Engagement ratios
        "like_view_ratio",
        "comment_view_ratio",
        "engagement_score",
        # Context
        "video_age_hours",
        "hours_tracked",
    ]
    
    # Only include features that exist
    available_features = [f for f in features if f in df.columns]
    
    final_df = df[available_features + [target_col]]
    final_df = base_features.clean_dataframe(final_df, fill_value=0)
    
    # Log feature statistics
    n_features = len(available_features)
    n_samples = len(final_df)
    logger.info(f"Features prepared: {n_features} features, {n_samples} samples")
    logger.info(f"Feature list: {available_features}")
    target_mean = final_df[target_col].mean()
    target_median = final_df[target_col].median()
    logger.info(f"Target: mean={target_mean:.0f}, median={target_median:.0f}")
    
    # Check for potential data issues
    zero_target = (final_df[target_col] == 0).sum()
    if zero_target > 0:
        logger.warning(f"{zero_target} samples have zero target views")
    
    return final_df


@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    target_col = VELOCITY_CONFIG.get("target", "views")
    
    # Skip detailed checks if dataset is too small
    if len(df) < 50:
        logger.warning(
            f"Dataset too small for meaningful integrity checks ({len(df)} samples). "
            "Skipping Deepchecks validation - will improve as data accumulates."
        )
        return "skipped_small_dataset.html", True
    
    validation_df = df.loc[:, df.nunique() > 1]
    
    dropped_cols = set(df.columns) - set(validation_df.columns)
    if dropped_cols:
        logger.warning(f"Skipping constant columns for integrity check: {dropped_cols}")

    if target_col not in validation_df.columns:
        logger.warning(
            "Target column seems constant! "
            "Adding it back for validation check."
        )
        validation_df[target_col] = df[target_col]

    # Identify categorical features that exist in the data
    potential_cat_features = ["publish_day", "publish_hour", "is_weekend"]
    cat_features = [f for f in potential_cat_features if f in validation_df.columns]
    
    ds = Dataset(
        validation_df,
        label=target_col,
        cat_features=cat_features,
    )

    integ_suite = data_integrity()
    result = integ_suite.run(ds)

    report_path = "velocity_integrity_report.html"
    result.save_as_html(report_path)

    if not result.passed():
        logger.warning("Data Integrity issues found (see report). Continuing...")
        repo_id = GLOBAL_CONFIG.get("hf_repo_id")
        if repo_id:
            try:
                uploader = ModelUploader(repo_id)
                uploader.upload_file(
                    report_path,
                    "reports/velocity_integrity_FAILED.html",
                )
                logger.info("Uploaded integrity report to HF Hub for review.")
            except Exception as e:
                logger.error(f"Failed to upload error report: {e}")
        
        # Return True to continue pipeline - integrity issues are warnings, not blockers
        return report_path, True

    return report_path, True


@task(name="Train XGBoost (AutoML)")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    target_col = VELOCITY_CONFIG.get("target", "views")

    X = df.drop(columns=[target_col])
    y = df[target_col]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    tuning_conf = VELOCITY_CONFIG.get("tuning", {})
    param_dist = tuning_conf.get("params", {})

    if not param_dist:
        logger.warning("No tuning params. Using defaults.")
        model = xgb.XGBRegressor(
            objective="reg:squarederror",
            n_jobs=-1,
            random_state=42,
        )
        model.fit(X_train, y_train)
    else:
        logger.info(f"Starting AutoML ({tuning_conf.get('n_iter', 10)} iters)...")
        base_model = xgb.XGBRegressor(
            objective="reg:squarederror",
            n_jobs=-1,
            random_state=42,
        )
        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=param_dist,
            n_iter=tuning_conf.get("n_iter", 10),
            cv=tuning_conf.get("cv", 3),
            scoring="r2",
            verbose=1,
            random_state=42,
            n_jobs=-1
        )
        search.fit(X_train, y_train)
        model = search.best_estimator_
        logger.info(f"Best Params: {search.best_params_}")

    preds = model.predict(X_test)
    eval_metrics = metrics.get_regression_metrics(y_test, preds)
    logger.info(f"Training Metrics: {eval_metrics}")

    return model, X_train, X_test, y_train, y_test, eval_metrics


@task(name="Deepchecks: Model Eval")
def run_evaluation_checks(model, X_train, X_test, y_train, y_test):
    target_col = VELOCITY_CONFIG.get("target", "views")
    
    # Identify categorical features that exist in the data
    potential_cat_features = ["publish_day", "publish_hour", "is_weekend"]
    cat_features = [f for f in potential_cat_features if f in X_train.columns]
    
    train_ds = Dataset(
        pd.concat([X_train, y_train], axis=1),
        label=target_col,
        cat_features=cat_features,
    )
    test_ds = Dataset(
        pd.concat([X_test, y_test], axis=1),
        label=target_col,
        cat_features=cat_features,
    )

    eval_suite = model_evaluation()
    result = eval_suite.run(train_dataset=train_ds, test_dataset=test_ds, model=model)
    report_path = "velocity_eval_report.html"
    result.save_as_html(report_path)
    return report_path


@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports):
    logger = get_run_logger()

    try:
        uploader = ModelUploader()
        repo_id = uploader.repo_id
    except ValueError as e:
        logger.warning(f"Skipping upload: {e}")
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("velocity/model.pkl")
    metric_name = VELOCITY_CONFIG.get("metric", "r2_score")

    passed, new_score, old_score = validator.compare_models(
        model, old_model, X_test, y_test, metric_name=metric_name
    )

    if passed:
        logger.info(f"Promoting model ({new_score:.4f} vs {old_score:.4f})")
        joblib.dump(model, "velocity_model.pkl")
        uploader = ModelUploader(repo_id)
        uploader.upload_file("velocity_model.pkl", "velocity/model.pkl")
        for k, v in reports.items():
            uploader.upload_file(v, f"reports/velocity_{k}_latest.html")
        return "PROMOTED"
    return "DISCARDED"


@task(name="Notify")
def notify(status, error_msg=None, metrics=None):
    msg = f"Pipeline finished. Error: {error_msg}" if error_msg else "Success"
    send_discord_alert(status, "Velocity Predictor", msg, metrics)


@flow(name="Train Velocity Predictor (V1)", log_prints=True)
def velocity_training_flow():
    logger = get_run_logger()
    run_metrics = {}
    try:
        raw_df = load_data()
        run_metrics["Rows"] = len(raw_df)

        processed_df = prepare_features(raw_df)
        run_metrics["Features"] = processed_df.shape[1] - 1

        integrity_path, _ = run_integrity_checks(processed_df)

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
            model,
            X_test,
            y_test,
            {"integrity": integrity_path, "eval": eval_path},
        )
        run_metrics["Deployment"] = status
        notify("SUCCESS", metrics=run_metrics)

    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        notify("FAILURE", error_msg=str(e), metrics=run_metrics)
        raise e


if __name__ == "__main__":
    velocity_training_flow()