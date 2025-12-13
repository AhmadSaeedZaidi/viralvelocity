import pandas as pd
import joblib
import os
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, RandomizedSearchCV
from prefect import flow, task, get_run_logger
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation

# --- Modular Imports ---
from training.feature_engineering import temporal_features
from training.evaluation import metrics
from training.evaluation.validators import ModelValidator
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"

def load_config():
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    return full_config.get("models", {}).get("viral", {}), full_config.get("global", {})

VIRAL_CONFIG, GLOBAL_CONFIG = load_config()

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
    df['timestamp'] = pd.to_datetime(df['discovered_at']).astype(int) // 10**9
    
    features_list = []
    for vid, group in df.groupby('video_id'):
        if len(group) < 2: continue
        
        group = group.sort_values('discovered_at')
        
        rank_diff = group['rank'].iloc[-1] - group['rank'].iloc[0]
        time_diff = group['timestamp'].iloc[-1] - group['timestamp'].iloc[0]
        velocity = rank_diff / (time_diff + 1)
        
        # Target: Currently in top 10?
        current_rank = group['rank'].iloc[-1]
        is_viral = 1 if current_rank <= 10 else 0
        
        features_list.append({
            'velocity': velocity,
            'start_rank': group['rank'].iloc[0],
            'is_viral': is_viral
        })
        
    final_df = pd.DataFrame(features_list)
    logger.info(f"Generated features for {len(final_df)} videos.")
    return final_df

@task(name="Deepchecks: Integrity")
def run_integrity(df: pd.DataFrame):
    ds = Dataset(df, label='is_viral', cat_features=[])
    integ = data_integrity()
    res = integ.run(ds)
    path = "viral_integrity.html"
    res.save_as_html(path)
    return path, res.passed()

@task(name="Train Logistic Regression")
def train_model(df: pd.DataFrame):
    X = df[['velocity', 'start_rank']]
    y = df['is_viral']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Tuning Config
    tuning_conf = VIRAL_CONFIG.get("tuning", {})
    
    if tuning_conf:
        base_model = LogisticRegression(max_iter=1000)
        search = RandomizedSearchCV(
            estimator=base_model,
            param_distributions=tuning_conf.get("params", {}),
            n_iter=tuning_conf.get("n_iter", 10),
            cv=tuning_conf.get("cv", 3),
            scoring='accuracy',
            n_jobs=-1,
            verbose=1
        )
        search.fit(X_train, y_train)
        model = search.best_estimator_
    else:
        model = LogisticRegression()
        model.fit(X_train, y_train)
    
    preds = model.predict(X_test)
    eval_metrics = metrics.get_classification_metrics(y_test, preds)
    
    return model, X_train, X_test, y_train, y_test, eval_metrics

@task(name="Deepchecks: Eval")
def run_eval(model, X_train, X_test, y_train, y_test):
    train_ds = Dataset(pd.concat([X_train, y_train], axis=1), label='is_viral')
    test_ds = Dataset(pd.concat([X_test, y_test], axis=1), label='is_viral')
    
    suite = model_evaluation()
    res = suite.run(train_ds, test_ds, model)
    path = "viral_eval.html"
    res.save_as_html(path)
    return path

@task(name="Validate & Upload")
def validate_and_upload(model, X_test, y_test, reports, current_metrics):
    logger = get_run_logger()
    
    # Initialize uploader (will use env vars HF_USERNAME/HF_MODELS)
    try:
        uploader = ModelUploader()
        repo_id = uploader.repo_id
    except ValueError as e:
        logger.warning(f"Skipping upload: {e}")
        return "SKIPPED"

    validator = ModelValidator(repo_id)
    old_model = validator.load_production_model("viral/model.pkl")
    
    # Compare F1 scores
    passed, new_score, old_score = validator.compare_models(
        model, old_model, X_test, y_test, metric_name="f1"
    )
    
    if passed:
        logger.info("New model is better or equal. Uploading...")
        joblib.dump(model, "viral_model.pkl")
        
        uploader.upload_file("viral_model.pkl", "viral/model.pkl")
        for k, v in reports.items():
            uploader.upload_file(v, f"reports/viral_{k}_latest.html")
        return "PROMOTED"
    else:
        logger.info("New model did not improve. Discarding.")
        return "DISCARDED"

@task(name="Notify")
def notify(status, error=None, metrics=None):
    send_discord_alert(status, "Viral Trend Classifier", 
                       f"Finished. {error if error else ''}", metrics)

@flow(name="Train Viral Classifier", log_prints=True)
def viral_training_flow():
    run_metrics = {}
    try:
        raw = load_data()
        df = prepare_features(raw)
        
        int_path, passed = run_integrity(df)
        if not passed: raise Exception("Integrity Failed")
        
        model, Xt, Xv, yt, yv, eval_metrics = train_model(df)
        run_metrics.update(eval_metrics)
        
        eval_path = run_eval(model, Xt, Xv, yt, yv)
        
        validate_and_upload(
            model, Xv, yv, 
            {"integrity": int_path, "eval": eval_path},
            eval_metrics
        )
        notify("SUCCESS", metrics=run_metrics)
        
    except Exception as e:
        notify("FAILURE", error=str(e), metrics=run_metrics)
        raise e

if __name__ == "__main__":
    viral_training_flow()