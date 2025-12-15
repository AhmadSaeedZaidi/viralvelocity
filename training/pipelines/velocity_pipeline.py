import joblib
import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostRegressor
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.compose import TransformedTargetRegressor
from sklearn.model_selection import train_test_split

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator

# --- Modular Imports ---
from training.feature_engineering import base_features, temporal_features, text_features
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
            full_config.get("models", {}).get("velocity", {}),
            full_config.get("global", {}),
        )
    except FileNotFoundError:
        return {}, {}


VELOCITY_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---


@task(retries=3, retry_delay_seconds=30, name="Load Video Data")
def load_data():
    logger = get_run_logger()
    logger.info("Starting data load for velocity pipeline (using search_discovery)")
    loader = DataLoader()
    
    # Use search_discovery with 2-hour minimum tracking (for early data)
    MIN_TRACKING_HOURS = 2
    df = loader.get_velocity_training_data(min_hours=MIN_TRACKING_HOURS)
    
    if df.empty:
        logger.warning("No data from search_discovery. Trying legacy fallback...")
        df = loader.get_training_pairs_flexible()
        
        if df.empty:
            raise ValueError(
                "No training data found. Ensure search_discovery and video_stats "
                "tables have data."
            )
    
    # Ensure no negative values for log transforms later
    df['target_views'] = df['target_views'].clip(lower=0)
    df['start_views'] = df['start_views'].clip(lower=0)
    
    logger.info(f"Loaded {len(df)} samples (min {MIN_TRACKING_HOURS}h tracking)")
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
    
    # --- 3. Growth-based Features (modular) ---
    df = base_features.calculate_growth_features(df)

    # --- 4. Text Features (modular) ---
    if "title" in df.columns:
        df = text_features.extract_title_features(df, title_col="title")

    # --- 5. Video Age (modular) ---
    df = temporal_features.calculate_video_age(df)
    
    # --- 6. Normalization (modular) ---
    df = base_features.normalize_features(df)

    # --- 7. Define Target ---
    target_col = VELOCITY_CONFIG.get("target", "views")
    df[target_col] = df["target_views"]

    # --- 8. Select Final Features ---
    features = [
        # Temporal
        "hour_sin", "hour_cos", "publish_day", "is_weekend",
        # Initial state (Log & Raw)
        "duration_seconds", "log_duration",
        "start_views", "log_start_views",
        "start_likes", "start_comments",
        # Engagement ratios
        "like_view_ratio", "comment_view_ratio", "engagement_score",
        # Growth Physics (New)
        "view_growth_rate", "log_view_growth", "relative_growth_rate",
        "interaction_velocity", "interaction_score",
        # Context
        "video_age_hours", "hours_tracked",
        # Text (If available)
        "title_len", "caps_ratio", "exclamation_count", "question_count", "has_digits",
        # Category (If available)
        "category_id"
    ]
    
    # Only include features that exist
    available_features = [f for f in features if f in df.columns]
    
    # Keep Category ID as int if it exists (for CatBoost)
    if "category_id" in df.columns:
        df["category_id"] = df["category_id"].fillna(-1).astype(int)

    final_df = df[available_features + [target_col]]
    final_df = base_features.clean_dataframe(final_df, fill_value=0)
    
    # Log feature statistics
    logger.info(f"Features prepared: {len(available_features)} features, {len(final_df)} samples")
    return final_df


@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    target_col = VELOCITY_CONFIG.get("target", "views")
    
    # Skip detailed checks if dataset is too small
    if len(df) < 50:
        logger.warning("Dataset too small for integrity checks. Skipping.")
        return "skipped_small_dataset.html", True
    
    validation_df = df.loc[:, df.nunique() > 1]
    
    # Identify categorical features
    potential_cat_features = ["publish_day", "is_weekend", "category_id"]
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
        logger.warning("Data Integrity issues found. Continuing...")
        # (Upload logic preserved from original)
        repo_id = GLOBAL_CONFIG.get("hf_repo_id")
        if repo_id:
            try:
                uploader = ModelUploader(repo_id)
                uploader.upload_file(report_path, "reports/velocity_integrity_FAILED.html")
            except Exception:
                pass
        return report_path, True

    return report_path, True


@task(name="Train CatBoost (Log Space)")
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

    logger.info("Training CatBoost with TransformedTargetRegressor (Log Space)...")

    base_model = CatBoostRegressor(
        iterations=1500,
        learning_rate=0.03,
        depth=6,
        l2_leaf_reg=3,
        loss_function='RMSE',
        verbose=0, # Silent to keep logs clean
        random_seed=42,
        allow_writing_files=False,
        cat_features=cat_features if cat_features else None
    )

    model = TransformedTargetRegressor(
        regressor=base_model,
        func=np.log1p,
        inverse_func=np.expm1
    )

    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    
    # Calculate metrics on REAL values
    eval_metrics = metrics.get_regression_metrics(y_test, preds)
    logger.info(f"Training Metrics (Real Space): {eval_metrics}")

    return model, X_train, X_test, y_train, y_test, eval_metrics


@task(name="Deepchecks: Model Eval")
def run_evaluation_checks(model, X_train, X_test, y_train, y_test):
    target_col = VELOCITY_CONFIG.get("target", "views")
    
    potential_cat_features = ["publish_day", "is_weekend", "category_id"]
    cat_features = [f for f in potential_cat_features if f in X_train.columns]
    
    # Deepchecks will call model.predict(), which returns REAL values now.
    # So we pass the REAL y_train/y_test.
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
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if not repo_id:
        logger.warning("No hf_repo_id - skipping upload.")
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("velocity/model.pkl")
    metric_name = VELOCITY_CONFIG.get("metric", "r2_score")

    # This works because our model wrapper returns real values, 
    # compatible with y_test (real values)
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