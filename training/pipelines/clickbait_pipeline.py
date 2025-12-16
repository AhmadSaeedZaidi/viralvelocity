import joblib
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import RandomizedSearchCV, train_test_split

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator
from training.feature_engineering import base_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# Load Config (Global)
with open("training/config/training_config.yaml", "r") as f:
    CONFIG = yaml.safe_load(f)

PIPELINE_CONFIG = CONFIG["models"]["clickbait"]

@task(name="Load Data")
def load_data():
    loader = DataLoader()
    df = loader.get_joined_data()
    
    # Deduplicate to fix data integrity issues (Conflicting Labels / Duplicates)
    if "video_id" in df.columns:
        df = df.drop_duplicates(subset=["video_id"], keep="last")
        
    return df

@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    
    # 1. Clean & Ratios (Needed for labeling)
    df = base_features.clean_dataframe(df)
    df = base_features.calculate_engagement_ratios(df)
    
    # 2. Labeling Logic (From Config)
    thresh = PIPELINE_CONFIG["labeling"]["engagement_threshold"]
    min_views = PIPELINE_CONFIG["labeling"]["min_views"]
    target_col = PIPELINE_CONFIG["target"]
    
    def label_clickbait(row):
        if row['views'] > min_views and row['engagement_score'] < thresh:
            return 1
        return 0
    
    df[target_col] = df.apply(label_clickbait, axis=1)
    
    # 3. Prepare Features (X)
    X = base_features.prepare_clickbait_features(df)
    
    # 4. Combine X and y
    final_df = pd.concat([X, df[target_col]], axis=1)
    
    logger.info(f"Features ready. Shape: {final_df.shape}")
    return final_df

@task(name="Deepchecks: Data Integrity")
def run_integrity(df: pd.DataFrame):
    logger = get_run_logger()
    target = PIPELINE_CONFIG["target"]
    
    # Create Deepchecks Dataset
    ds = Dataset(df, label=target, cat_features=[])
    
    integ_suite = data_integrity()
    result = integ_suite.run(ds)
    
    report_path = "clickbait_integrity_report.html"
    result.save_as_html(report_path)
    
    if not result.passed():
        logger.warning("Data Integrity checks failed (see report). Continuing...")
        
        # Upload failed report for inspection
        try:
            repo_id = CONFIG.get("global", {}).get("hf_repo_id")
            if repo_id:
                uploader = ModelUploader(repo_id)
                repo_path = "reports/clickbait_integrity_FAILED.html"
                uploader.upload_file(report_path, repo_path)
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")
            
        return report_path, False
        
    return report_path, True

@task(name="Hyperparameter Tuning")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()
    
    target_col = PIPELINE_CONFIG["target"]
    X = df.drop(columns=[target_col])
    y = df[target_col]
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, 
        test_size=PIPELINE_CONFIG["test_size"], 
        random_state=PIPELINE_CONFIG["random_state"]
    )
    
    # Tuning Config
    tuning_conf = PIPELINE_CONFIG["tuning"]
    
    base_model = RandomForestClassifier(random_state=42)
    
    search = RandomizedSearchCV(
        estimator=base_model,
        param_distributions=tuning_conf["params"],
        n_iter=tuning_conf["n_iter"],
        cv=tuning_conf["cv"],
        scoring='f1',
        n_jobs=-1,
        verbose=1
    )
    
    logger.info("Starting Hyperparameter Optimization...")
    search.fit(X_train, y_train)
    
    best_model = search.best_estimator_
    logger.info(f"Best Params: {search.best_params_}")

    # Calculate metrics
    y_pred = best_model.predict(X_test)
    eval_metrics = metrics.get_classification_metrics(y_test, y_pred)
    logger.info(f"Validation Metrics: {eval_metrics}")
    
    return best_model, X_train, X_test, y_train, y_test, eval_metrics

@task(name="Deepchecks: Model Eval")
def run_eval(model, X_train, X_test, y_train, y_test):
    target_col = PIPELINE_CONFIG["target"]
    
    train_ds = Dataset(
        pd.concat([X_train, y_train], axis=1),
        label=target_col,
        cat_features=[],
    )
    test_ds = Dataset(
        pd.concat([X_test, y_test], axis=1),
        label=target_col,
        cat_features=[],
    )
    
    suite = model_evaluation()
    result = suite.run(train_dataset=train_ds, test_dataset=test_ds, model=model)
    path = "clickbait_eval.html"
    result.save_as_html(path)
    return path

@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports):
    logger = get_run_logger()
    repo_id = CONFIG["global"]["hf_repo_id"]
    if not repo_id:
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("clickbait/model.pkl")
    metric_name = PIPELINE_CONFIG["metric"]

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
    
    logger.info("Model did not improve.")
    return "DISCARDED"

@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Finished. {error if error else ''}"
    send_discord_alert(status, "Clickbait Pipeline", msg, metrics)

@flow(name="Clickbait Pipeline (Modular)", log_prints=True)
def clickbait_pipeline():
    run_metrics = {}
    try:
        # 1. ETL
        raw_df = load_data()
        df = prepare_features(raw_df)
        
        # 2. Integrity Check (Deepchecks)
        integrity_path, passed = run_integrity(df)
        if not passed:
            print("Data Integrity Failed. Continuing pipeline as requested...")
        
        # 3. Train & Tune (AutoML)
        best_model, Xt, Xv, yt, yv, eval_metrics = train_model(df)
        run_metrics.update(eval_metrics)

        # 4. Generate Eval Report
        eval_path = run_eval(best_model, Xt, Xv, yt, yv)

        # 5. Validation & Deployment
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