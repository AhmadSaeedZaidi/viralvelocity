import joblib
import numpy as np
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
from training.feature_engineering import temporal_features, text_features
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

    df["stat_time"] = pd.to_datetime(df["stat_time"])
    df["published_at"] = pd.to_datetime(df["published_at"])
    
    # --- 1. Static Features (Time & Text) ---
    df = temporal_features.add_date_features(df, "published_at")
    
    # Calculate cyclic time features if available
    if "publish_hour" in df.columns:
        df["hour_sin"] = np.sin(2 * np.pi * df["publish_hour"] / 24)
        df["hour_cos"] = np.cos(2 * np.pi * df["publish_hour"] / 24)
    else:
        df["hour_sin"] = 0
        df["hour_cos"] = 0

    # Extract text features if title exists
    if "title" in df.columns:
        try:
            df = text_features.extract_title_features(df, title_col="title")
        except Exception:
            df["title"] = df["title"].fillna("")
            df["title_len"] = df["title"].str.len()
            # vectorized caps ratio: count uppercase letters / (length + 1)
            df["caps_ratio"] = df["title"].str.count(r"[A-Z]") / (df["title_len"] + 1)
            df["has_digits"] = df["title"].str.contains(r"\\d").astype(int)
    else:
        df["title_len"] = 0
        df["caps_ratio"] = 0
        df["has_digits"] = 0

    total_videos = df["video_id"].nunique()
    skipped_single_snapshot = 0
    features_list = []
    
    all_velocities = []
    
    # Group by video to calculate velocities. We iterate once to define the
    # threshold, then again to build the dataset. Two passes keep code clear.
    
    # First pass: Calculate View Velocities for Labeling
    for vid, group in df.groupby("video_id"):
        if len(group) < 2:
            skipped_single_snapshot += 1
            continue

        group = group.sort_values("stat_time")
        
        # Calculate time span in hours
        start_time = group["stat_time"].iloc[0]
        end_time = group["stat_time"].iloc[-1]
        time_diff_hours = (end_time - start_time).total_seconds() / 3600.0
        
        if time_diff_hours < 2:  # Need at least 2 hours of data
            skipped_single_snapshot += 1
            continue
        
        # View velocity = views gained per hour
        view_diff = group["views"].iloc[-1] - group["views"].iloc[0]
        view_velocity = view_diff / (time_diff_hours + 0.1)
        all_velocities.append(view_velocity)
    
    if not all_velocities:
        raise ValueError("No valid videos with sufficient time span found.")
    
    # Define "viral" threshold as top 20% of view velocities
    viral_threshold = pd.Series(all_velocities).quantile(0.80)
    logger.info(f"Viral threshold (top 20%): {viral_threshold:.0f} views/hour")
    
    # Second pass: Build Features
    for vid, group in df.groupby("video_id"):
        if len(group) < 2:
            continue

        group = group.sort_values("stat_time")

        start_row = group.iloc[0]
        end_row = group.iloc[-1]

        # Time delta in hours
        delta = end_row["stat_time"] - start_row["stat_time"]
        time_diff_hours = delta.total_seconds() / 3600.0

        if time_diff_hours < 2:
            continue

        # Core metrics
        start_views = start_row["views"]
        end_views = end_row["views"]
        view_diff = end_views - start_views
        view_velocity = view_diff / (time_diff_hours + 0.1)

        # Engagement Velocities (slope)
        denom = time_diff_hours + 0.1
        like_velocity = (end_row["likes"] - start_row["likes"]) / denom
        comment_velocity = (end_row["comments"] - start_row["comments"]) / denom

        # Engagement ratios (quality) - use END values for current status summary
        like_ratio = end_row["likes"] / (end_views + 1)
        comment_ratio = end_row["comments"] / (end_views + 1)

        # Age at START of tracking
        delta_age = start_row["stat_time"] - start_row["published_at"]
        video_age_hours = delta_age.total_seconds() / 3600.0
        video_age_hours = max(0.5, video_age_hours)  # Safety clip

        # 1. Initial Virality Slope (Log-Log Slope at T=0)
        initial_virality_slope = np.log1p(start_views) / np.log1p(video_age_hours)

        # 2. Interaction Density (Log Space)
        interaction_num = np.log1p(start_row["likes"] + start_row["comments"] * 2)
        interaction_den = np.log1p(start_views + 1)
        interaction_density = interaction_num / interaction_den

        # Duration
        dur_val = start_row.get("duration_seconds", 0)
        duration = dur_val if pd.notna(dur_val) else 0

        # Label
        is_viral = 1 if view_velocity >= viral_threshold else 0

        # Static features (from first row)
        hour_sin = start_row.get("hour_sin", 0)
        hour_cos = start_row.get("hour_cos", 0)
        title_len = start_row.get("title_len", 0)
        caps_ratio = start_row.get("caps_ratio", 0)
        has_digits = start_row.get("has_digits", 0)

        features_list.append({
            # Target
            "is_viral": is_viral,
            
            # Explicitly Excluded (Leak): "view_velocity"
            
            # Predictive Features
            "like_velocity": like_velocity,
            "comment_velocity": comment_velocity,
            "start_views": start_views,
            "log_start_views": np.log1p(start_views), # Log transform included
            "like_ratio": like_ratio,
            "comment_ratio": comment_ratio,
            "video_age_hours": video_age_hours,
            "duration_seconds": duration,
            "hours_tracked": time_diff_hours,
            "snapshots": len(group),
            
            # Novel Features
            "initial_virality_slope": initial_virality_slope,
            "interaction_density": interaction_density,
            "hour_sin": hour_sin,
            "hour_cos": hour_cos,
            "title_len": title_len,
            "caps_ratio": caps_ratio,
            "has_digits": has_digits
        })

    logger.info(
        f"Feature engineering: {total_videos} unique videos, "
        f"kept {len(features_list)} samples."
    )

    if not features_list:
        raise ValueError("No training samples found.")

    final_df = pd.DataFrame(features_list)
    
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
                 uploader.upload_file(path, "viral/reports/integrity_latest.html")
            else:
                 logger.warning("Integrity checks failed.")
                 uploader.upload_file(path, "viral/reports/integrity_FAILED.html")
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
            uploader.upload_file(path, "viral/reports/eval_latest.html")
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
    metric_name = VIRAL_CONFIG.get("metric", "accuracy")

    passed, new_score, old_score = validator.compare_models(
        model, old_model, X_test, y_test, metric_name=metric_name
    )

    if passed:
        logger.info(f"Promoting model. New {metric_name}: {new_score:.4f}")
        joblib.dump(model, "viral_model.pkl")
        uploader = ModelUploader(repo_id)
        uploader.upload_file("viral_model.pkl", "viral/model.pkl")
        uploader.upload_reports(reports, folder="viral/reports")
        return "PROMOTED"
    
    logger.info("Model did not improve.")
    return "DISCARDED"


@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Finished. {error if error else ''}"
    send_discord_alert(status, "Viral Trend Classifier", msg, metrics)


@flow(name="Train Viral Classifier", log_prints=True)
def viral_training_flow():
    logger = get_run_logger()
    metrics = {}
    try:
        raw = load_data()
        metrics["Raw_Rows"] = len(raw)
        
        df = prepare_features(raw)
        metrics["Training_Samples"] = len(df)
        metrics["Features"] = len(df.columns) - 1

        int_path, passed = run_integrity(df)
        if not passed:
            logger.warning("Data Integrity failed. Continuing...")

        model, Xt, Xv, yt, yv, eval_metrics = train_model(df)
        metrics.update(eval_metrics)

        eval_path = run_eval(model, Xt, Xv, yt, yv)

        status = validate_and_upload(
            model, Xv, yv, {"integrity": int_path, "eval": eval_path}
        )
        metrics["Deployment"] = status
        notify("SUCCESS", metrics=metrics)

    except Exception as e:
        notify("FAILURE", error=str(e), metrics=metrics)
        raise e


if __name__ == "__main__":
    viral_training_flow()