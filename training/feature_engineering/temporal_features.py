import numpy as np
import pandas as pd


def add_date_features(df: pd.DataFrame, date_col='published_at') -> pd.DataFrame:
    """Extracts cyclic time features."""
    if date_col not in df.columns:
        return df
        
    df[date_col] = pd.to_datetime(df[date_col])
    
    df['publish_hour'] = df[date_col].dt.hour
    df['publish_day'] = df[date_col].dt.dayofweek
    df['is_weekend'] = df['publish_day'].isin([5, 6]).astype(int)
    
    # Cyclical Time Features
    df["hour_sin"] = np.sin(2 * np.pi * df["publish_hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["publish_hour"] / 24)
    
    return df

def calculate_video_age(
    df: pd.DataFrame,
    start_time_col: str = 'start_time',
    published_at_col: str = 'published_at',
) -> pd.DataFrame:
    """Calculates video age in hours at the time of first observation."""
    if start_time_col not in df.columns or published_at_col not in df.columns:
        return df
        
    df[published_at_col] = pd.to_datetime(df[published_at_col])
    df[start_time_col] = pd.to_datetime(df[start_time_col])
    
    time_delta = (df[start_time_col] - df[published_at_col]).dt.total_seconds()
    df["video_age_hours"] = (time_delta / 3600.0).clip(lower=0)
    
    return df

def calculate_velocity_features(df: pd.DataFrame, window=5) -> pd.DataFrame:
    """Calculates channel momentum (rolling averages)."""
    if 'channel_id' not in df.columns:
        return df
        
    df = df.sort_values(['channel_id', 'published_at'])
    
    # Lagged features: Avg views of PREVIOUS n videos
    df['channel_avg_views_recent'] = df.groupby('channel_id')['views'].transform(
        lambda x: x.rolling(window=window, min_periods=1).mean().shift(1)
    ).fillna(0)
    
    return df