import joblib
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity
from mlxtend.frequent_patterns import apriori, association_rules
from mlxtend.preprocessing import TransactionEncoder
from prefect import flow, get_run_logger, task

from training.evaluation import metrics
from training.evaluation.validators import ModelValidator
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
            full_config.get("models", {}).get("tags", {}),
            full_config.get("global", {}),
        )
    except FileNotFoundError:
        return {}, {}


TAGS_CONFIG, GLOBAL_CONFIG = load_config()

# --- Tasks ---


@task(retries=3, retry_delay_seconds=30, name="Load High Performing Data")
def load_data():
    logger = get_run_logger()
    logger.info("Loading tag data")
    loader = DataLoader()
    df = loader.get_joined_data()
    
    if df.empty:
        raise ValueError("No data found for tag recommendation training.")
    
    # Filter top performing videos based on configuration
    percentile = TAGS_CONFIG.get("params", {}).get("top_percentile", 0.90)
    if 'views' in df.columns:
        threshold = df['views'].quantile(percentile)
        top_df = df[df['views'] >= threshold].copy()
        logger.info(
            f"Loaded {len(df)} rows. Filtering top {int((1-percentile)*100)}% "
            f"(> {threshold:.0f} views) -> {len(top_df)} rows."
        )
    else:
        logger.warning("'views' column missing. Using all data.")
        top_df = df.copy()
    
    if len(top_df) < 10:
        raise ValueError(
            "Not enough high-performing videos to generate meaningful rules."
        )
        
    return top_df


@task(name="Feature Engineering")
def prepare_features(df: pd.DataFrame):
    logger = get_run_logger()
    logger.info("Preprocessing tags into transactions")
    dataset = []
    
    # Use modular text processing if applicable or standard logic
    if 'tags' in df.columns:
        # text_features.get_tags_list is defined in the uploaded file content
        # It handles string splitting and cleaning
        for tag_str in df['tags'].dropna():
            tag_list = text_features.get_tags_list(tag_str)
            # Only include transactions with at least 2 tags
            if len(tag_list) >= 2:
                dataset.append(tag_list)
    
    logger.info(f"Created {len(dataset)} valid transactions")
    return dataset


@task(name="Deepchecks: Data Integrity")
def run_integrity_checks(df: pd.DataFrame):
    logger = get_run_logger()
    
    ds = Dataset(df, cat_features=[])
    result = data_integrity().run(ds)
    
    report_path = "tags_integrity_report.html"
    result.save_as_html(report_path)
    
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if repo_id:
        try:
            uploader = ModelUploader(repo_id)
            if result.passed():
                uploader.upload_file(
                    report_path, "tags/reports/tags_integrity_latest.html"
                )
            else:
                logger.warning("Data Integrity checks failed.")
                uploader.upload_file(
                    report_path, "tags/reports/tags_integrity_FAILED.html"
                )
        except Exception as e:
            logger.warning(f"Failed to upload integrity report: {e}")
            
    return report_path


@task(name="Generate Rules (Apriori)")
def train_model(dataset):
    logger = get_run_logger()
    
    if not dataset:
        raise ValueError("Dataset is empty after preprocessing tags.")
    
    te = TransactionEncoder()
    te_ary = te.fit_transform(dataset)
    df_trans = pd.DataFrame(te_ary, columns=te.columns_)
    
    # Configurable support/confidence
    params = TAGS_CONFIG.get("params", {})
    min_support = params.get("min_support", 0.01)
    min_lift = params.get("min_threshold", 1.2)
    min_conf = params.get("min_confidence", 0.5)
    
    logger.info(f"Running Apriori with min_support={min_support}...")
    
    # Apriori Algorithm
    frequent_itemsets = apriori(
        df_trans, min_support=min_support, use_colnames=True
    )
    
    # Adaptive Support: If strict support yields nothing, relax it
    if frequent_itemsets.empty:
        logger.warning(
            "No frequent itemsets found! Relaxing support to 0.005..."
        )
        frequent_itemsets = apriori(
            df_trans, min_support=0.005, use_colnames=True
        )
        if frequent_itemsets.empty:
            raise ValueError(
                "No frequent itemsets found even with low support."
            )
        
    # Generate Association Rules
    rules = association_rules(
        frequent_itemsets, metric="lift", min_threshold=min_lift
    )
    
    # Filter by Confidence
    filtered_rules = rules[rules['confidence'] > min_conf].copy()
    
    # Convert frozen sets to lists for serialization
    filtered_rules['antecedents'] = filtered_rules['antecedents'].apply(
        lambda x: list(x)
    )
    filtered_rules['consequents'] = filtered_rules['consequents'].apply(
        lambda x: list(x)
    )
    
    # Calculate Metrics using modular function
    rule_metrics = metrics.get_association_rule_metrics(filtered_rules)
    
    logger.info(f"Generated Rules Metrics: {rule_metrics}")
    return filtered_rules, rule_metrics


@task(name="Validate Rules")
def run_evaluation_checks(rules, rule_metrics):
    logger = get_run_logger()
    
    # 1. Quantity Check
    if rule_metrics["rule_count"] < 5:
        logger.warning(
            f"Too few rules generated ({rule_metrics['rule_count']}). "
            "Pipeline might need more data."
        )
        return False
        
    # 2. Quality Check (Lift)
    if rule_metrics["avg_lift"] < 1.05:
        logger.warning(
            f"Average lift is too low ({rule_metrics['avg_lift']}). "
            "Rules are weak."
        )
        return False
        
    logger.info("Validation Passed.")
    return True


@task(name="Validate & Upload")
def validate_and_upload(rules, rule_metrics, is_valid, integrity_report):
    logger = get_run_logger()
    
    if not is_valid:
        return "DISCARDED"
    
    repo_id = GLOBAL_CONFIG.get("hf_repo_id")
    if not repo_id:
        return "SKIPPED"

    try:
        validator = ModelValidator(repo_id)
        old_rules = validator.load_production_model("tags/rules.pkl")
        
        if old_rules is not None:
            old_metrics = metrics.get_association_rule_metrics(old_rules)
            logger.info(f"Previous Model Metrics: {old_metrics}")
            
        uploader = ModelUploader(repo_id)
        
        local_path = "tag_rules.pkl"
        joblib.dump(rules, local_path)
        
        uploader.upload_file(local_path, "tags/rules.pkl")
        
        reports = {"integrity": integrity_report}
        uploader.upload_reports(reports, folder="tags/reports")
        
        return "PROMOTED"
    except Exception as e:
        logger.warning(f"Upload failed: {e}")
        return "ERROR"


@task(name="Notify")
def notify(status, error_msg=None, metrics=None):
    msg = f"Finished. Error: {error_msg}" if error_msg else "Success"
    send_discord_alert(status, "Tag Recommender", msg, metrics)


@flow(name="Train Tag Recommender", log_prints=True)
def tags_training_flow():
    logger = get_run_logger()
    run_metrics = {}
    try:
        # 1. Load Data
        df = load_data()
        run_metrics["Top_Videos"] = len(df)
        
        # 2. Check Input (Deepchecks)
        report_path = run_integrity_checks(df)
            
        # 3. Feature Engineering (Preprocess Tags)
        dataset = prepare_features(df)
        run_metrics["Transactions"] = len(dataset)
        
        # 4. Generate Rules & Metrics
        rules, rule_metrics = train_model(dataset)
        run_metrics.update(rule_metrics)
        
        # 5. Validate Rules
        is_valid = run_evaluation_checks(rules, rule_metrics)
        
        # 6. Upload
        status = validate_and_upload(rules, rule_metrics, is_valid, report_path)
        
        run_metrics["Deployment"] = status
        notify("SUCCESS", metrics=run_metrics)
        
    except Exception as e:
        logger.error(f"Pipeline crashed: {e}")
        notify("FAILURE", error_msg=str(e), metrics=run_metrics)
        raise e


if __name__ == "__main__":
    tags_training_flow()