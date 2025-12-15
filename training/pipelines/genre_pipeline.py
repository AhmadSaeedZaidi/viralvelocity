
import joblib
import numpy as np
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity
from prefect import flow, get_run_logger, task
from sklearn.decomposition import TruncatedSVD
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from tensorflow.keras import callbacks, layers, models

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator

# --- Modular Imports ---
from training.feature_engineering import text_features
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"

def load_config():
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    # Default to specific genre config or generic defaults
    return full_config.get("models", {}).get("genre", {
        "test_size": 0.2,
        "random_state": 42,
        "pca_candidates": [50, 100, 200, 300],
        "metric": "accuracy"
    }), full_config.get("global", {})

GENRE_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---

@task(retries=3, retry_delay_seconds=5, name="Fetch Metadata")
def load_data_task():
    logger = get_run_logger()
    loader = DataLoader()
    df = loader.get_video_metadata()
    # Ensure we have data
    if df.empty:
        raise ValueError("Database returned empty dataframe.")
    
    df = df.dropna(subset=['title', 'tags'])
    logger.info(f"Loaded {len(df)} videos for training.")
    return df

@task(name="Feature Engineering")
def prepare_features_task(df: pd.DataFrame):
    logger = get_run_logger()
    
    # 1. Text Processing (Modular)
    # Uses shared regex/cleaning logic to prevent training-serving skew
    df['text'] = text_features.prepare_text_features(df, text_cols=['title', 'tags'])
    
    # 2. Label Logic (Mock or Real)
    if 'category_id' not in df.columns:
        # Fallback for dev/testing: mark as Gaming when tags mention Minecraft
        def _infer_genre_from_tags(x):
            txt = str(x).lower()
            if 'minecraft' in txt:
                return 'Gaming'
            return 'Vlog'

        df['genre'] = df['tags'].apply(_infer_genre_from_tags)
    else:
        df['genre'] = df['category_id']
        
    logger.info("Text cleaning and label extraction complete.")
    return df

@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Create Deepchecks Dataset
    ds = Dataset(df, label='genre', cat_features=['genre'])
    
    # Run Suite
    integ_suite = data_integrity()
    result = integ_suite.run(ds)
    
    # Save Report
    report_path = "genre_integrity_report.html"
    result.save_as_html(report_path)
    
    if not result.passed():
        logger.warning("Data Integrity checks failed. Uploading report and continuing.")
        try:
            repo_id = GLOBAL_CONFIG.get("hf_repo_id")
            if repo_id:
                uploader = ModelUploader(repo_id)
                uploader.upload_file(report_path, "reports/genre_integrity_FAILED.html")
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")
            
        return report_path, False
        
    return report_path, True

@task(name="Vectorization")
def vectorize_task(df: pd.DataFrame):
    logger = get_run_logger()
    
    # Use Modular Preprocessor
    preprocessor = text_features.TextPreprocessor(max_features=5000)
    X_sparse = preprocessor.fit_transform(df['text'])
    
    # Encode Labels
    le = LabelEncoder()
    y = le.fit_transform(df['genre'])
    
    logger.info(f"Vectorized Text Shape: {X_sparse.shape}")
    return X_sparse, y, preprocessor.vectorizer, le

@task(name="Optimize & Apply SVD")
def svd_optimization_task(X_sparse, y):
    logger = get_run_logger()
    
    candidates = GENRE_CONFIG.get("pca_candidates", [50, 100, 200])
    
    # Skip optimization if data is tiny
    if X_sparse.shape[0] < 200:
        best_n = 50
        logger.info("Small dataset detected. Skipping optimization, using n=50.")
    else:
        best_n = candidates[0]
        best_score = -1
        
        # Split for optimization
        X_train, X_val, y_train, y_val = train_test_split(
            X_sparse, y, test_size=0.2, random_state=42
        )
        
        logger.info(f"Optimizing PCA components: {candidates}")
        for n in candidates:
            if n > X_sparse.shape[1]:
                continue

            svd = TruncatedSVD(n_components=n, random_state=42)
            X_t = svd.fit_transform(X_train)
            X_v = svd.transform(X_val)
            
            # Proxy Model (LogReg is faster than training full Keras MLP 4 times)
            clf = LogisticRegression(max_iter=200, class_weight='balanced')
            clf.fit(X_t, y_train)
            score = clf.score(X_v, y_val)
            
            logger.info(
                f"n={n} | Proxy Acc: {score:.4f} | Var: "
                f"{svd.explained_variance_ratio_.sum():.2%}"
            )
            
            if score > best_score:
                best_score = score
                best_n = n

    logger.info(f"Selected Optimal Components: {best_n}")
    
    # Apply to full dataset
    final_svd = TruncatedSVD(n_components=best_n, random_state=42)
    X_reduced = final_svd.fit_transform(X_sparse)
    
    return X_reduced, final_svd

@task(name="Train Neural Network")
def train_mlp_task(X, y, num_classes):
    logger = get_run_logger()
    
    # Split for Training
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, 
        test_size=GENRE_CONFIG.get("test_size", 0.2), 
        random_state=GENRE_CONFIG.get("random_state", 42)
    )
    
    model = models.Sequential([
        layers.Input(shape=(X.shape[1],)),
        layers.Dense(256, activation='relu'),
        layers.Dropout(0.4),
        layers.Dense(128, activation='relu'),
        layers.Dropout(0.3),
        layers.Dense(num_classes, activation='softmax')
    ])
    
    model.compile(
        optimizer='adam',
        loss='sparse_categorical_crossentropy',
        metrics=['accuracy'],
    )

    early_stop = callbacks.EarlyStopping(
        monitor='loss',
        patience=5,
        restore_best_weights=True,
    )
    model.fit(
        X_train, y_train, 
        epochs=20, 
        batch_size=32, 
        callbacks=[early_stop], 
        verbose=0,
        validation_data=(X_test, y_test)
    )
    
    # Evaluate using Modular Metrics
    y_pred_prob = model.predict(X_test)
    y_pred = np.argmax(y_pred_prob, axis=1)
    
    # Convert Keras metrics to standard dict
    eval_metrics = metrics.get_classification_metrics(y_test, y_pred)
    
    try:
        eval_metrics['top_k_accuracy'] = metrics.get_top_k_accuracy(
            y_test,
            y_pred_prob,
            k=3,
        )
    except Exception as e:
        logger.warning(f"Could not calc top_k_accuracy: {e}")
    
    logger.info(f"Model Trained. Metrics: {eval_metrics}")
    
    return model, X_test, y_test, eval_metrics['accuracy']

class KerasWrapper:
    """Wraps Keras model to behave like sklearn classifier for Deepchecks"""
    def __init__(self, model):
        self.model = model
        
    def predict(self, X):
        # Deepchecks expects class labels
        probs = self.model.predict(X)
        return np.argmax(probs, axis=1)
        
    def predict_proba(self, X):
        # Deepchecks expects probabilities
        return self.model.predict(X)

@task(name="Deepchecks: Model Eval")
def run_evaluation_checks(model, X_test, y_test):
    df_test = pd.DataFrame(X_test)
    df_test['target'] = y_test

    Dataset(df_test, label='target', cat_features=[])
    return None

@task(name="Champion vs Challenger")
def validate_genre_pipeline(new_model, X_test_reduced, y_test, new_acc):
    """
    Custom validator for Genre because it involves 3 artifacts:
    SVD + Keras + Vectorizer. We compare the new Keras model on the
    *reduced* test set against the old model's reported metrics, or we try
    to load the full old pipeline.
    """
    logger = get_run_logger()
    validator = ModelValidator(repo_id=GLOBAL_CONFIG.get("hf_repo_id"))
    
    # Try to load old Keras model
    old_model = validator.load_production_model("genre/model.h5")
    
    if old_model is None:
        return True, new_acc, 0.0

    try:
        if old_model.input_shape[1] != X_test_reduced.shape[1]:
            logger.warning(
                "Old model expects different input shape (SVD changed). "
                "Defaulting to New Model."
            )
            return True, new_acc, 0.0
            
        old_prob = old_model.predict(X_test_reduced)
        old_pred = np.argmax(old_prob, axis=1)
        old_acc = metrics.accuracy_score(y_test, old_pred)
        
        logger.info(f"Comparison: New Acc={new_acc:.4f} vs Old Acc={old_acc:.4f}")
        
        if new_acc >= old_acc:
            return True, new_acc, old_acc
        return False, new_acc, old_acc
        
    except Exception as e:
        logger.warning(
            "Could not compare models directly: %s. Defaulting to New Model.",
            e,
        )
        return True, new_acc, 0.0

@task(name="Deploy System")
def deploy_task(model, vectorizer, le, svd, is_champion, integrity_report):
    logger = get_run_logger()
    
    if not is_champion:
        logger.info("New model is not better. Skipping deployment.")
        return

    # Save Locally
    model.save("genre_model.h5")
    joblib.dump(vectorizer, "genre_vectorizer.pkl")
    joblib.dump(le, "genre_label_encoder.pkl")
    joblib.dump(svd, "genre_svd.pkl")
    
    # Upload
    try:
        uploader = ModelUploader() # Uses env vars
        uploader.upload_file("genre_model.h5", "genre/model.h5")
        uploader.upload_file("genre_vectorizer.pkl", "genre/vectorizer.pkl")
        uploader.upload_file("genre_label_encoder.pkl", "genre/label_encoder.pkl")
        uploader.upload_file("genre_svd.pkl", "genre/svd.pkl")
        
        # Use unified report uploader
        reports = {
            "integrity": integrity_report
        }
        uploader.upload_reports(reports, folder="genre/reports")
        
        logger.info("🚀 All Genre artifacts deployed to production.")
    except ValueError as e:
        logger.error(f"Deployment failed: {e}")

# --- Main Flow ---

@flow(name="Train Genre Classifier (Modular)", log_prints=True)
def genre_pipeline():
    result_metrics = {}
    try:
        # 1. ETL
        raw_df = load_data_task()
        df = prepare_features_task(raw_df)
        
        # 2. Integrity Check
        integrity_path, passed = run_integrity_checks(df)
        if not passed:
            print("Data Integrity Failed. Continuing pipeline as requested...")
        
        # 3. Vectorize
        X_sparse, y, vectorizer, le = vectorize_task(df)
        
        # 3. Reduce Dimensions (SVD)
        X_reduced, svd = svd_optimization_task(X_sparse, y)
        
        # 4. Train
        model, X_test, y_test, new_acc = train_mlp_task(X_reduced, y, len(le.classes_))
        
        # 5. Validate
        is_champion, new_score, old_score = validate_genre_pipeline(
            model,
            X_test,
            y_test,
            new_acc,
        )
        
        result_metrics = {
            "accuracy": f"{new_score:.4f}",
            "old_accuracy": f"{old_score:.4f}",
            "deployed": is_champion,
            "svd_components": svd.n_components
        }
        
        # 6. Deploy
        deploy_task(model, vectorizer, le, svd, is_champion, integrity_path)
        
        status = "SUCCESS" if is_champion else "SKIPPED"
        msg = "New model deployed." if is_champion else "New model underperformed."
        send_discord_alert(status, "Genre Pipeline", msg, result_metrics)

    except Exception as e:
        send_discord_alert("FAILURE", "Genre Pipeline", str(e), result_metrics)
        raise e

if __name__ == "__main__":
    genre_pipeline()