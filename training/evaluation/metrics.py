import numpy as np
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    mean_absolute_error, mean_squared_error, r2_score
)

def get_classification_metrics(y_true, y_pred, y_prob=None):
    """
    Returns a dictionary of standard classification metrics.
    Handles both binary and multi-class (weighted) automatically.
    """
    average_type = 'binary' if len(np.unique(y_true)) == 2 else 'weighted'
    
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "f1": float(f1_score(y_true, y_pred, average=average_type)),
        "precision": float(precision_score(y_true, y_pred, average=average_type)),
        "recall": float(recall_score(y_true, y_pred, average=average_type))
    }
    return metrics

def get_regression_metrics(y_true, y_pred):
    """
    Returns dictionary of regression metrics.
    Includes MAPE (Mean Absolute Percentage Error) which is crucial for View counts.
    """
    # Avoid division by zero for MAPE
    y_true_safe = np.where(y_true == 0, 1, y_true) 
    mape = np.mean(np.abs((y_true - y_pred) / y_true_safe)) * 100

    metrics = {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
        "mape_percent": float(mape)
    }
    return metrics

def get_top_k_accuracy(y_true, y_prob, k=3):
    """
    For Genre Classification: Did the correct genre appear in the top K predictions?
    """
    if y_prob is None:
        return None
        
    # Get indices of top k probabilities
    top_k_preds = np.argsort(y_prob, axis=1)[:, -k:]
    
    # Check if true label is in top k
    matches = [y in pred for y, pred in zip(y_true, top_k_preds)]
    return float(np.mean(matches))