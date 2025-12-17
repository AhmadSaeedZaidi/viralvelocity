from typing import Any, Dict, List

import pandas as pd


def format_large_number(num: float) -> str:
    """
    Formats large numbers (views, likes) into human-readable strings.
    Example: 1,500,000 -> "1.5M", 45,000 -> "45K"
    """
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(int(num))


def clean_tags_input(tag_string: str) -> List[str]:
    """
    Parses a comma-separated string of tags into a clean list.
    Removes whitespace and empty entries.
    """
    if not tag_string:
        return []
    return [t.strip().lower() for t in tag_string.split(",") if t.strip()]


def api_response_to_dataframe(response_list: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Converts a list of API response dictionaries into a flattened DataFrame.
    Useful for the 'Batch Prediction' or 'History' views.
    """
    if not response_list:
        return pd.DataFrame()

    # Flatten nested dictionaries if necessary (e.g., metadata)
    df = pd.json_normalize(response_list)

    # Convert timestamps if present
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    return df


def calculate_mape(y_true: pd.Series, y_pred: pd.Series) -> float:
    """
    Calculates Mean Absolute Percentage Error (MAPE).
    Used for evaluating Regression models (Velocity Predictor).
    """
    mask = y_true != 0
    return (abs(y_true[mask] - y_pred[mask]) / y_true[mask]).mean() * 100
