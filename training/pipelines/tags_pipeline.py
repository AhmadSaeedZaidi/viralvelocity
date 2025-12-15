
import joblib
import pandas as pd
import yaml
from deepchecks.tabular import Dataset
from deepchecks.tabular.suites import data_integrity
from mlxtend.frequent_patterns import apriori, association_rules
from mlxtend.preprocessing import TransactionEncoder
from prefect import flow, get_run_logger, task

# --- Modular Imports ---
from training.utils.data_loader import DataLoader
from training.utils.model_uploader import ModelUploader
from training.utils.notifications import send_discord_alert

# --- Configuration ---
CONFIG_PATH = "training/config/training_config.yaml"

def load_config():
    with open(CONFIG_PATH, "r") as f:
        full_config = yaml.safe_load(f)
    return full_config.get("models", {}).get("tags", {}), full_config.get("global", {})

TAGS_CONFIG, GLOBAL_CONFIG = load_config()

@task(retries=3, name="Load High Performing Data")
def load_data():
    loader = DataLoader()
    df = loader.get_joined_data()
    # Filter top 10%
    threshold = df['views'].quantile(0.90)
    top_df = df[df['views'] >= threshold].copy()
    return top_df

@task(name="Preprocess Tags")
def preprocess_tags(df: pd.DataFrame):
    # Expand string tags to list
    dataset = []
    for tags in df['tags'].dropna():
        tag_list = [t.strip().lower() for t in tags.split(',') if t.strip()]
        dataset.append(tag_list)
    return dataset

@task(name="Deepchecks: Input Integrity")
def check_input_integrity(df: pd.DataFrame):
    # We check the raw dataframe before transformation
    ds = Dataset(df, cat_features=[])
    integ_suite = data_integrity()
    result = integ_suite.run(ds)
    
    report_path = "tags_integrity_report.html"
    result.save_as_html(report_path)
    return report_path, result.passed()

@task(name="Generate Rules (Apriori)")
def generate_rules(dataset):
    logger = get_run_logger()
    
    te = TransactionEncoder()
    te_ary = te.fit_transform(dataset)
    df_trans = pd.DataFrame(te_ary, columns=te.columns_)
    
    # Configurable support/confidence
    params = TAGS_CONFIG.get("params", {})
    min_support = params.get("min_support", 0.01)
    min_lift = params.get("min_threshold", 1.2)
    
    # Apriori
    frequent_itemsets = apriori(df_trans, min_support=min_support, use_colnames=True)
    
    if frequent_itemsets.empty:
        raise ValueError("No frequent itemsets found! Try lowering min_support.")
        
    # Rules
    rules = association_rules(frequent_itemsets, metric="lift", min_threshold=min_lift)
    filtered_rules = rules[rules['confidence'] > 0.5]
    
    logger.info(f"Generated {len(filtered_rules)} rules.")
    return filtered_rules

@task(name="Validate Rules")
def validate_rules(rules):
    logger = get_run_logger()
    
    # 1. Quantity Check
    if len(rules) < 10:
        logger.warning("Too few rules generated (<10).")
        return False
        
    # 2. Quality Check (Lift)
    avg_lift = rules['lift'].mean()
    if avg_lift < 1.1:
        logger.warning(f"Average lift is too low ({avg_lift:.2f}). Rules might be weak.")
        return False
        
    logger.info(f"Validation Passed. Avg Lift: {avg_lift:.2f}")
    return True

@task(name="Validate & Upload")
def validate_and_upload(rules, report_path, is_valid):
    logger = get_run_logger()
    
    if not is_valid:
        logger.warning("Validation failed. Skipping upload.")
        return "DISCARDED"
    
    # Initialize uploader (will use env vars HF_USERNAME/HF_MODELS)
    try:
        uploader = ModelUploader()
    except ValueError as e:
        logger.warning(f"Skipping upload: {e}")
        return "SKIPPED"

    local_path = "tag_rules.pkl"
    joblib.dump(rules, local_path)
    
    uploader.upload_file(local_path, "tags/rules.pkl")
    uploader.upload_file(report_path, "reports/tags_integrity_latest.html")
    
    return "PROMOTED"

@task(name="Notify")
def notify(status, error_msg=None, metrics=None):
    send_discord_alert(status, "Tag Recommender", 
                       f"Pipeline finished. {error_msg if error_msg else ''}", 
                       metrics)

@flow(name="Train Tag Recommender", log_prints=True)
def tags_training_flow():
    metrics = {}
    try:
        df = load_data()
        metrics["Top Videos"] = len(df)
        
        report_path, passed = check_input_integrity(df)
        if not passed:
            raise Exception("Data Integrity Checks Failed")
            
        dataset = preprocess_tags(df)
        rules = generate_rules(dataset)
        metrics["Rules Generated"] = len(rules)
        
        is_valid = validate_rules(rules)
        
        validate_and_upload(rules, report_path, is_valid)
        
        status = "SUCCESS" if is_valid else "SKIPPED"
        notify(status, metrics=metrics)
        
    except Exception as e:
        notify("FAILURE", error_msg=str(e), metrics=metrics)
        raise e

if __name__ == "__main__":
    tags_training_flow()