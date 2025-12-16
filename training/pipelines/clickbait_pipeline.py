import joblib
import numpy as np
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import RandomizedSearchCV, train_test_split

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator
from training.feature_engineering import base_features, temporal_features, text_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

try:
    with open("training/config/training_config.yaml", "r") as f:
        CONFIG = yaml.safe_load(f)
    PIPELINE_CONFIG = CONFIG.get("models", {}).get("clickbait", {})
except Exception:
    CONFIG = {}
    PIPELINE_CONFIG = {}

@task(name="Load Data")
def load_data():
    logger = get_run_logger()
    loader = DataLoader()
    df = loader.get_joined_data()
    
    initial_len = len(df)
    
    if "video_id" in df.columns:
        df = df.drop_duplicates(subset=["video_id"], keep="last")
    
    df = df.reset_index(drop=True)
    
    dropped = initial_len - len(df)
    if dropped > 0:
        logger.info(f"Dropped {dropped} duplicate videos. Remaining: {len(df)}")
        
    return df

@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    
    # 1. Base Cleaning & Ratios (Needed ONLY for labeling, not training)
    df = base_features.clean_dataframe(df)
    df = base_features.calculate_engagement_ratios(df)
    
    # 2. Labeling Logic (Ground Truth)
    thresh = PIPELINE_CONFIG.get("labeling", {}).get("engagement_threshold", 0.05)
    min_views = PIPELINE_CONFIG.get("labeling", {}).get("min_views", 100)
    target_col = PIPELINE_CONFIG.get("target", "is_clickbait")
    
    def label_clickbait(row):
        # High views but Low engagement = Clickbait (The "Empty Calorie" metric)
        if row['views'] > min_views and row['engagement_score'] < thresh:
            return 1
        return 0
    
    df[target_col] = df.apply(label_clickbait, axis=1)
    
    # 3. Training Features (Predictors)
    # We MUST NOT use engagement ratios here, as they define the target.
    # We predict clickbait based on Metadata (Title, Time) alone.
    
    # A. Text Features (The core of clickbait detection)
    if "title" in df.columns:
        try:
            df = text_features.extract_title_features(df, title_col="title")
        except Exception:
            # Fallback
            df["title"] = df["title"].fillna("")
            df["title_len"] = df["title"].str.len()
            df["caps_ratio"] = df["title"].apply(lambda x: sum(1 for c in str(x) if c.isupper()) / (len(str(x)) + 1))
            df["exclamation_count"] = df["title"].str.count("!")
            df["question_count"] = df["title"].str.count("\?")
            df["has_digits"] = df["title"].str.contains(r'\d').astype(int)
            
    # B. Temporal Features
    df = temporal_features.add_date_features(df, date_col="published_at")
    if "publish_hour" in df.columns:
        df["hour_sin"] = np.sin(2 * np.pi * df["publish_hour"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["publish_hour"] / 24)
    else:
        df["hour_sin"] = 0
        df["hour_cos"] = 0

    # Select final feature set
    # Note: explicitly excluding 'views', 'likes', 'ratios' to prevent leakage.
    feature_cols = [
        "title_len", "caps_ratio", "exclamation_count", "question_count", "has_digits", # Text
        "hour_sin", "hour_cos", "publish_day", "is_weekend" # Time
    ]
    
    # Only keep available columns
    feature_cols = [c for c in feature_cols if c in df.columns]
    
    X = df[feature_cols].reset_index(drop=True)
    y = df[target_col].reset_index(drop=True)
    
    final_df = pd.concat([X, y], axis=1)
    
    # Deduplicate feature rows
    before_dedup = len(final_df)
    final_df = final_df.drop_duplicates()
    
    if before_dedup - len(final_df) > 0:
        logger.info(f"Dropped {before_dedup - len(final_df)} duplicate feature rows.")
    
    logger.info(f"Features ready. Shape: {final_df.shape}")
    return final_df

@task(name="Deepchecks: Data Integrity")
def run_integrity(df: pd.DataFrame):
    logger = get_run_logger()
    target = PIPELINE_CONFIG.get("target", "is_clickbait")
    
    ds = Dataset(df, label=target, cat_features=[])
    result = data_integrity().run(ds)
    
    report_path = "clickbait_integrity_report.html"
    result.save_as_html(report_path)
    
    repo_id = CONFIG.get("global", {}).get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            if result.passed():
                uploader.upload_file(report_path, "clickbait/reports/integrity_latest.html")
            else:
                logger.warning("Integrity checks failed.")
                uploader.upload_file(report_path, "clickbait/reports/integrity_FAILED.html")
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")
            
    return report_path, result.passed()

@task(name="Hyperparameter Tuning")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    target_col = PIPELINE_CONFIG.get("target", "is_clickbait")
    X = df.drop(columns=[target_col])
    y = df[target_col]
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, 
        test_size=PIPELINE_CONFIG.get("test_size", 0.2), 
        random_state=PIPELINE_CONFIG.get("random_state", 42)
    )
    
    # Tuning Config
    tuning_conf = PIPELINE_CONFIG.get("tuning", {})
    
    # Upgrade: Gradient Boosting is better for dense numerical/ordinal features
    base_model = GradientBoostingClassifier(random_state=42)
    
    if tuning_conf:
        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=tuning_conf.get("params", {}),
            n_iter=tuning_conf.get("n_iter", 10),
            cv=tuning_conf.get("cv", 3),
            scoring='f1',
            n_jobs=-1,
            verbose=1
        )
        logger.info("Starting Hyperparameter Optimization...")
        search.fit(X_train, y_train)
        best_model = search.best_estimator_
        logger.info(f"Best Params: {search.best_params_}")
    else:
        best_model = base_model
        best_model.fit(X_train, y_train)

    y_pred = best_model.predict(X_test)
    eval_metrics = metrics.get_classification_metrics(y_test, y_pred)
    logger.info(f"Validation Metrics: {eval_metrics}")
    
    return best_model, X_train, X_test, y_train, y_test, eval_metrics

@task(name="Deepchecks: Model Eval")
def run_eval(model, X_train, X_test, y_train, y_test):
    target_col = PIPELINE_CONFIG.get("target", "is_clickbait")
    
    train_ds = Dataset(
        pd.concat([X_train.reset_index(drop=True), y_train.reset_index(drop=True)], axis=1),
        label=target_col,
        cat_features=[],
    )
    test_ds = Dataset(
        pd.concat([X_test.reset_index(drop=True), y_test.reset_index(drop=True)], axis=1),
        label=target_col,
        cat_features=[],
    )
    
    result = model_evaluation().run(train_dataset=train_ds, test_dataset=test_ds, model=model)
    path = "clickbait_eval.html"
    result.save_as_html(path)
    
    repo_id = CONFIG.get("global", {}).get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            uploader.upload_file(path, "clickbait/reports/eval_latest.html")
        except Exception:
            pass
            
    return path

@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports):
    logger = get_run_logger()
    repo_id = CONFIG.get("global", {}).get("hf_repo_id")
    if not repo_id:
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("clickbait/model.pkl")
    metric_name = PIPELINE_CONFIG.get("metric", "f1_score")

    passed, new_score, old_score = validator.compare_models(
        model, old_model, X_test, y_test, metric_name=metric_name
    )

    if passed:
        logger.info(f"Promoting model. New {metric_name}: {new_score:.4f}")
        joblib.dump(model, "clickbait_model.pkl")
        uploader = ModelUploader(repo_id)
        uploader.upload_file("clickbait_model.pkl", "clickbait/model.pkl")
        uploader.upload_reports(reports, folder="clickbait/reports")
        return "PROMOTED"
    
    return "DISCARDED"

@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Finished. {error if error else ''}"
    send_discord_alert(status, "Clickbait Pipeline", msg, metrics)

@flow(name="Clickbait Pipeline (Modular)", log_prints=True)
def clickbait_pipeline():
    run_metrics = {}
    try:
        raw_df = load_data()
        df = prepare_features(raw_df)
        
        integrity_path, passed = run_integrity(df)
        if not passed:
            print("Data Integrity Failed. Continuing pipeline...")
        
        best_model, Xt, Xv, yt, yv, eval_metrics = train_model(df)
        run_metrics.update(eval_metrics)

        eval_path = run_eval(best_model, Xt, Xv, yt, yv)

        status = validate_and_upload(
            best_model, Xv, yv, {"integrity": integrity_path, "eval": eval_path}
        )
        
        run_metrics["Deployment"] = status
        notify("SUCCESS", metrics=run_metrics)
            
    except Exception as e:
        notify("FAILURE", error=str(e), metrics=run_metrics)
        raise e

if __name__ == "__main__":
    clickbait_pipeline()