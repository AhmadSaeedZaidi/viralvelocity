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


@task(retries=3, name="Load Trending History")
def load_data():
    loader = DataLoader()
    df = loader.get_trending_history()
    if df.empty:
        raise ValueError("No trending history found.")
    return df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()

    # Velocity Calc
    df["timestamp"] = pd.to_datetime(df["discovered_at"])

    features_list = []
    for vid, group in df.groupby("video_id"):
        if len(group) < 2:
            continue

        group = group.sort_values("discovered_at")

        rank_diff = group["rank"].iloc[-1] - group["rank"].iloc[0]
        time_diff_hours = (
            group["timestamp"].iloc[-1] - group["timestamp"].iloc[0]
        ).total_seconds() / 3600.0
        velocity = rank_diff / (time_diff_hours + 0.1)

        current_rank = group["rank"].iloc[-1]
        start_rank = group["rank"].iloc[0]
        min_rank = group["rank"].min()
        rank_volatility = group["rank"].std() if len(group) > 2 else 0.0
        appearances = len(group)
        
        is_viral = 1 if current_rank <= 10 else 0

        features_list.append(
            {
                "velocity": velocity,
                "start_rank": start_rank,
                "min_rank": min_rank,
                "rank_volatility": rank_volatility,
                "appearances": appearances,
                "hours_tracked": time_diff_hours,
                "is_viral": is_viral,
            }
        )

    final_df = pd.DataFrame(features_list)
    
    viral_count = final_df["is_viral"].sum()
    total_count = len(final_df)
    logger.info(
        f"Generated features for {total_count} videos. "
        f"Viral: {viral_count} ({100*viral_count/total_count:.1f}%), "
        f"Not Viral: {total_count - viral_count} ({100*(total_count-viral_count)/total_count:.1f}%)"
    )
    
    return final_df


@task(name="Deepchecks: Integrity")
def run_integrity(df: pd.DataFrame):
    logger = get_run_logger()
    ds = Dataset(df, label="is_viral", cat_features=[])
    integ = data_integrity()
    res = integ.run(ds)
    path = "viral_integrity.html"
    res.save_as_html(path)

    if not res.passed():
        logger.warning("Integrity checks failed (Report saved).")
        return path, False
    return path, True


@task(name="Train Logistic Regression")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    feature_cols = ["velocity", "start_rank", "min_rank", "rank_volatility", "appearances", "hours_tracked"]
    X = df[feature_cols]
    y = df["is_viral"]
    
    logger.info(f"Training with features: {feature_cols}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    
    # Tuning Config
    tuning_conf = VIRAL_CONFIG.get("tuning", {})
    
    if tuning_conf:
        # Always use class_weight="balanced" as base to handle imbalanced classes
        base_model = LogisticRegression(max_iter=1000, class_weight="balanced")
        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=tuning_conf.get("params", {}),
            n_iter=tuning_conf.get("n_iter", 10),
            cv=tuning_conf.get("cv", 3),
            scoring='f1',  # Use F1 instead of accuracy for imbalanced classification
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
    
    # Calculate full suite of classification metrics
    eval_metrics = metrics.get_classification_metrics(y_test, preds)
    
    # Log detailed report to help debug 0 metrics issue
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
        for k, v in reports.items():
            uploader.upload_file(v, f"reports/viral_{k}_latest.html")
        return "PROMOTED"
    
    logger.info("Model did not improve.")
    return "DISCARDED"


@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Finished. {error if error else ''}"
    send_discord_alert(status, "Viral Trend Classifier", msg, metrics)


@flow(name="Train Viral Classifier", log_prints=True)
def viral_training_flow():
    metrics = {}
    try:
        raw = load_data()
        df = prepare_features(raw)

        int_path, passed = run_integrity(df)
        if not passed:
            print("Data Integrity Warning - Check Reports")

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