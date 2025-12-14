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
from training.feature_engineering import temporal_features
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
    logger.info("Starting data load for velocity pipeline")
    loader = DataLoader()
    # 7-day forecast pairs
    df = loader.get_training_pairs(target_hours=168)

    if df.empty:
        raise ValueError("Database returned empty dataframe! No 7-day history found.")

    logger.info(f"Loaded {len(df)} training pairs.")
    return df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    logger.debug("Preparing features for velocity model")
    
    # 1. Date Features
    df = temporal_features.add_date_features(df, date_col="published_at")

    # 2. Map 'Start' snapshot data to Model Feature names
    df["likes"] = df["start_likes"]
    df["comments"] = df["start_comments"]

    # 3. Target: For this training, we treat 'target_views' as the label
    target_col = VELOCITY_CONFIG.get("target", "views")
    df[target_col] = df["target_views"]

    features = [
        "duration_seconds",
        "publish_hour",
        "publish_day",
        "start_views",  # Critical: The velocity at T=0 (or T=24h)
        "likes",
        "comments",
    ]

    # Clean up
    final_df = df[features + [target_col]].fillna(0)

    logger.info(f"Features prepared. Shape: {final_df.shape}")
    return final_df


@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    target_col = VELOCITY_CONFIG.get("target", "views")
    ds = Dataset(df, label=target_col, cat_features=["publish_day", "publish_hour"])

    integ_suite = data_integrity()
    result = integ_suite.run(ds)

    report_path = "velocity_integrity_report.html"
    result.save_as_html(report_path)

    if not result.passed():
        logger.error("Data Integrity checks failed!")
        return report_path, False

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

    # --- Hyperparameter Tuning (AutoML) ---
    tuning_conf = VELOCITY_CONFIG.get("tuning", {})
    param_dist = tuning_conf.get("params", {})

    if not param_dist:
        logger.warning("No tuning parameters found in config. Using defaults.")
        model = xgb.XGBRegressor(
            objective="reg:squarederror", n_jobs=-1, random_state=42
        )
        model.fit(X_train, y_train)
    else:
        logger.info(
            f"RandomizedSearchCV with {tuning_conf.get('n_iter', 10)} iterations"
        )

        base_model = xgb.XGBRegressor(
            objective="reg:squarederror", n_jobs=-1, random_state=42
        )

        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=param_dist,
            n_iter=tuning_conf.get("n_iter", 10),
            cv=tuning_conf.get("cv", 3),
            scoring="r2",  # Optimizing for R2 Score
            verbose=1,
            random_state=42,
            n_jobs=-1,
        )

        search.fit(X_train, y_train)
        model = search.best_estimator_

        logger.info(f"Best Hyperparameters: {search.best_params_}")
        logger.info(f"Best Validation Score (R2): {search.best_score_:.4f}")

    # --- Final Evaluation ---
    preds = model.predict(X_test)

    # Use modular metrics
    eval_metrics = metrics.get_regression_metrics(y_test, preds)

    logger.info(f"Model Training Complete. Metrics: {eval_metrics}")

    return model, X_train, X_test, y_train, y_test, eval_metrics


@task(name="Deepchecks: Model Eval")
def run_evaluation_checks(model, X_train, X_test, y_train, y_test):
    target_col = VELOCITY_CONFIG.get("target", "views")
    # Regression evaluation suite
    train_ds = Dataset(
        pd.concat([X_train, y_train], axis=1),
        label=target_col,
        cat_features=["publish_day", "publish_hour"],
    )
    test_ds = Dataset(
        pd.concat([X_test, y_test], axis=1),
        label=target_col,
        cat_features=["publish_day", "publish_hour"],
    )

    eval_suite = model_evaluation()
    result = eval_suite.run(train_dataset=train_ds, test_dataset=test_ds, model=model)

    report_path = "velocity_eval_report.html"
    result.save_as_html(report_path)

    return report_path


@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports):
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
    metric_name = VELOCITY_CONFIG.get("metric", "r2_score")
    passed, new_score, old_score = validator.compare_models(
        model, old_model, X_test, y_test, metric_name=metric_name
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
    msg = f"Pipeline finished. Error: {error_msg}" if error_msg else "Success"
    send_discord_alert(status, "Velocity Predictor", msg, metrics)


# --- Main Flow ---


@flow(name="Train Velocity Predictor (V1)", log_prints=True)
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