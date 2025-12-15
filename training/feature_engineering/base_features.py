import numpy as np
import pandas as pd


def clean_dataframe(df: pd.DataFrame, fill_value=0) -> pd.DataFrame:
    """Basic cleaning: infinite values, NaNs."""
    # Replace inf with NaN, then fill NaNs
    return df.replace([np.inf, -np.inf], np.nan).fillna(fill_value)

def calculate_engagement_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Generates standard YouTube engagement metrics."""
    # Prevent division by zero
    safe_views = df['views'].replace(0, 1)
    
    df['like_view_ratio'] = df['likes'] / safe_views
    df['comment_view_ratio'] = df['comments'] / safe_views
    df['engagement_score'] = (df['likes'] + (df['comments'] * 2)) / safe_views
    return df

def calculate_growth_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calculates view/engagement growth rates and velocities."""
    # Ensure hours_tracked exists and avoid division by zero
    if 'hours_tracked' not in df.columns:
        return df
        
    hours = df["hours_tracked"] + 0.1
    
    # View Growth
    if 'target_views' in df.columns and 'start_views' in df.columns:
        df["view_growth_rate"] = (
            df["target_views"] - df["start_views"]
        ) / hours
        df["log_view_growth"] = (
            np.log1p(df["target_views"]) - np.log1p(df["start_views"])
        )
        # Relative Growth (Normalized by start size)
        df["relative_growth_rate"] = (
            df["target_views"] - df["start_views"]
        ) / (df["start_views"] + 1)

    # Engagement Velocity
    if "end_likes" in df.columns and "start_likes" in df.columns:
        df["like_growth_rate"] = (df["end_likes"] - df["start_likes"]) / hours
        
    if "end_comments" in df.columns and "start_comments" in df.columns:
        df["comment_growth_rate"] = (df["end_comments"] - df["start_comments"]) / hours
        
    # Interaction Velocity (Weighted)
    if "start_likes" in df.columns and "start_comments" in df.columns:
        df["interaction_score"] = (
            (df["start_likes"] * 1.0) + (df["start_comments"] * 3.0)
        )
        df["interaction_velocity"] = df["interaction_score"] / hours
        
    return df

def normalize_features(df: pd.DataFrame) -> pd.DataFrame:
    """Applies log normalization to skewed features."""
    if "start_views" in df.columns:
        df["log_start_views"] = np.log1p(df["start_views"])
        
    if "duration_seconds" in df.columns:
        df["log_duration"] = np.log1p(df["duration_seconds"])
        
    return df

def prepare_anomaly_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepares features specifically for anomaly detection.
    Uses log-transformed views for magnitude and ratios for quality.
    """
    # Clean and basic ratios
    df = clean_dataframe(df)
    df = calculate_engagement_ratios(df)
    
    # Log transform views (Magnitude)
    df['log_views'] = np.log1p(df['views'])
    
    # Select orthogonal features
    features = [
        'log_views',          # Magnitude
        'like_view_ratio',    # Quality 1
        'comment_view_ratio'  # Quality 2
    ]
    
    return df[features].fillna(0)