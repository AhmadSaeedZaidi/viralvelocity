import pandas as pd
import numpy as np

def clean_dataframe(df: pd.DataFrame, fill_value=0) -> pd.DataFrame:
    """Basic cleaning: infinite values, NaNs."""
    return df.replace([np.inf, -np.inf], np.nan).fillna(fill_value)

def calculate_engagement_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Generates standard YouTube engagement metrics."""
    # Prevent division by zero
    safe_views = df['views'].replace(0, 1)
    
    df['like_view_ratio'] = df['likes'] / safe_views
    df['comment_view_ratio'] = df['comments'] / safe_views
    df['engagement_score'] = (df['likes'] + (df['comments'] * 2)) / safe_views
    return df