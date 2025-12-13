import pandas as pd

def add_date_features(df: pd.DataFrame, date_col='published_at') -> pd.DataFrame:
    """Extracts cyclic time features."""
    if date_col not in df.columns:
        return df
        
    df[date_col] = pd.to_datetime(df[date_col])
    
    df['publish_hour'] = df[date_col].dt.hour
    df['publish_day'] = df[date_col].dt.dayofweek
    df['is_weekend'] = df['publish_day'].isin([5, 6]).astype(int)
    
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