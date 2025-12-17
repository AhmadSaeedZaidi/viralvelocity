import joblib
import numpy as np
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity, model_evaluation
from prefect import flow, get_run_logger, task
from sklearn.decomposition import TruncatedSVD
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from tensorflow.keras import callbacks, layers, models

from training.evaluation import metrics
from training.feature_engineering import text_features
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
            full_config.get("models", {}).get("genre", {}),
            full_config.get("global", {}),
        )
    except FileNotFoundError:
        return {}, {}


GENRE_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---


@task(retries=3, name="Load Video Metadata")
def load_data():
    logger = get_run_logger()
    loader = DataLoader()
    df = loader.get_video_metadata()

    if df.empty:
        raise ValueError("No training data found.")

    df = df.dropna(subset=["title", "tags"])
    logger.info(f"Loaded {len(df)} videos")
    return df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()

    # 1. Text Processing
    df["text"] = text_features.prepare_text_features(df, text_cols=["title", "tags"])

    if "category_id" in df.columns:
        df["genre"] = df["category_id"]
    else:

        def _infer_genre(x):
            txt = str(x).lower()
            if "minecraft" in txt:
                return "Gaming"
            return "Vlog"

        df["genre"] = df["tags"].apply(_infer_genre)

    logger.info(f"Features prepared. Samples: {len(df)}")
    return df


@task(name="Deepchecks: Integrity")
def run_integrity(df: pd.DataFrame):
    logger = get_run_logger()
    ds = Dataset(df[["text", "genre"]], label="genre", cat_features=[])
    integ = data_integrity()
    res = integ.run(ds)
    path = "genre_integrity.html"
    res.save_as_html(path)
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            if res.passed():
                uploader.upload_reports({"integrity": path}, folder="genre/reports")
            else:
                logger.warning("Integrity checks failed.")
                failed_path = path.replace(".html", "_FAILED.html")
                import os

                os.rename(path, failed_path)
                uploader.upload_reports(
                    {"integrity": failed_path}, folder="genre/reports"
                )
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")

    return path, res.passed()


@task(name="Train Neural Network")
def train_model(df: pd.DataFrame):
    logger = get_run_logger()

    X = df["text"]
    y = df["genre"]

    # Encode Labels
    le = LabelEncoder()
    y_enc = le.fit_transform(y)
    num_classes = len(le.classes_)

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=0.2, random_state=42
    )

    # 1. Vectorize (TF-IDF)
    # Using modular TextPreprocessor if available, else manual TfidfVectorizer
    preprocessor = text_features.TextPreprocessor(max_features=5000)
    X_train_vec = preprocessor.fit_transform(X_train)
    X_test_vec = preprocessor.transform(X_test)

    # 2. SVD Optimization
    candidates = GENRE_CONFIG.get("pca_candidates", [50, 100, 200])
    best_n = 50

    # Simple AutoML loop to find best SVD components using LogisticRegression proxy
    if X_train_vec.shape[0] > 200:
        best_score = -1
        Xt_sub, Xv_sub, yt_sub, yv_sub = train_test_split(
            X_train_vec, y_train, test_size=0.2, random_state=42
        )

        for n in candidates:
            if n > X_train_vec.shape[1]:
                continue
            svd_tmp = TruncatedSVD(n_components=n, random_state=42)
            Xt_red = svd_tmp.fit_transform(Xt_sub)
            Xv_red = svd_tmp.transform(Xv_sub)

            clf = LogisticRegression(max_iter=200, class_weight="balanced")
            clf.fit(Xt_red, yt_sub)
            score = clf.score(Xv_red, yv_sub)

            if score > best_score:
                best_score = score
                best_n = n
        logger.info(f"Optimal SVD components: {best_n}")

    # Apply optimal SVD
    svd = TruncatedSVD(n_components=best_n, random_state=42)
    X_train_red = svd.fit_transform(X_train_vec)
    X_test_red = svd.transform(X_test_vec)

    # 3. Train MLP (Keras)
    model = models.Sequential(
        [
            layers.Input(shape=(best_n,)),
            layers.Dense(256, activation="relu"),
            layers.Dropout(0.4),
            layers.Dense(128, activation="relu"),
            layers.Dropout(0.3),
            layers.Dense(num_classes, activation="softmax"),
        ]
    )

    model.compile(
        optimizer="adam",
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )

    early_stop = callbacks.EarlyStopping(
        monitor="loss", patience=5, restore_best_weights=True
    )

    model.fit(
        X_train_red,
        y_train,
        epochs=20,
        batch_size=32,
        callbacks=[early_stop],
        verbose=0,
        validation_data=(X_test_red, y_test),
    )

    # Evaluate
    y_pred_prob = model.predict(X_test_red)
    y_pred = np.argmax(y_pred_prob, axis=1)

    # Calculate Metrics
    eval_metrics = metrics.get_classification_metrics(y_test, y_pred)
    logger.info(f"Validation Metrics: {eval_metrics}")

    artifacts = {
        "model": model,
        "vectorizer": preprocessor.vectorizer,
        "svd": svd,
        "le": le,
    }

    return artifacts, X_train_red, X_test_red, y_train, y_test, eval_metrics


@task(name="Deepchecks: Eval")
def run_eval(artifacts, X_train, X_test, y_train, y_test):
    model = artifacts["model"]

    # Wrap Keras for Deepchecks
    class KerasWrapper:
        def __init__(self, model):
            self.model = model

        def predict(self, X):
            return np.argmax(self.model.predict(X), axis=1)

        def predict_proba(self, X):
            return self.model.predict(X)

    wrapped_model = KerasWrapper(model)

    # Deepchecks needs a dataframe with feature names
    cols = [f"pc_{i}" for i in range(X_train.shape[1])]
    df_train = pd.DataFrame(X_train, columns=cols)
    df_test = pd.DataFrame(X_test, columns=cols)

    # Create datasets
    train_ds = Dataset(
        pd.concat([df_train, pd.Series(y_train, name="target")], axis=1),
        label="target",
        cat_features=[],
    )
    test_ds = Dataset(
        pd.concat([df_test, pd.Series(y_test, name="target")], axis=1),
        label="target",
        cat_features=[],
    )

    suite = model_evaluation()
    res = suite.run(train_dataset=train_ds, test_dataset=test_ds, model=wrapped_model)
    path = "genre_eval.html"
    res.save_as_html(path)

    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            uploader.upload_reports({"eval": path}, folder="genre/reports")
        except Exception:
            pass

    return path


@task(name="Validate & Upload")
def validate_and_upload(artifacts, X_test, y_test, reports, new_metrics):
    logger = get_run_logger()
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if not repo_id:
        return "SKIPPED"

    # Use standardized validator
    # validator = ModelValidator(repo_id)

    # Wrap Keras for validation
    class KerasWrapper:
        def __init__(self, model):
            self.model = model

        def predict(self, X):
            return np.argmax(self.model.predict(X), axis=1)

    # wrapped_model = KerasWrapper(artifacts["model"])

    # Load old model (if possible) - Note: Keras loading is complex,
    # so we might skip direct comparison
    # For now, we stick to the absolute threshold check as a "supervised"
    # check against a baseline

    passed = True
    acc = new_metrics.get("accuracy", 0)
    if acc < 0.2:  # Very low bar for safety
        logger.warning(f"Model accuracy too low ({acc:.2f}). Discarding.")
        passed = False

    if passed:
        uploader = ModelUploader(repo_id)

        # Save & Upload All Artifacts
        artifacts["model"].save("genre_model.h5")
        joblib.dump(artifacts["vectorizer"], "genre_vectorizer.pkl")
        joblib.dump(artifacts["le"], "genre_label_encoder.pkl")
        joblib.dump(artifacts["svd"], "genre_svd.pkl")

        uploader.upload_file("genre_model.h5", "genre/model.h5")
        uploader.upload_file("genre_vectorizer.pkl", "genre/vectorizer.pkl")
        uploader.upload_file("genre_label_encoder.pkl", "genre/label_encoder.pkl")
        uploader.upload_file("genre_svd.pkl", "genre/svd.pkl")

        uploader.upload_reports(reports, folder="genre/reports")
        logger.info(f"Promoted model with accuracy: {acc:.4f}")
        return "PROMOTED"

    return "DISCARDED"


@task(name="Notify")
def notify(status, error=None, metrics=None):
    msg = f"Finished. {error if error else ''}"
    send_discord_alert(status, "Genre Classifier", msg, metrics)


@flow(name="Train Genre Classifier", log_prints=True)
def genre_training_flow():
    logger = get_run_logger()
    run_metrics = {}
    try:
        raw = load_data()
        run_metrics["Raw_Rows"] = len(raw)

        df = prepare_features(raw)
        run_metrics["Training_Samples"] = len(df)

        int_path, passed = run_integrity(df)
        if not passed:
            logger.warning("Data Integrity failed. Continuing...")

        artifacts, Xt, Xv, yt, yv, eval_metrics = train_model(df)
        run_metrics.update(eval_metrics)

        eval_path = run_eval(artifacts, Xt, Xv, yt, yv)

        status = validate_and_upload(
            artifacts, Xv, yv, {"integrity": int_path, "eval": eval_path}, eval_metrics
        )
        run_metrics["Deployment"] = status
        notify("SUCCESS", metrics=run_metrics)

    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        notify("FAILURE", error=str(e), metrics=run_metrics)
        raise e


if __name__ == "__main__":
    genre_training_flow()
