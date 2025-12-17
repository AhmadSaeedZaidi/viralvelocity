import numpy as np
import pandas as pd

from training.feature_engineering import temporal_features, text_features


def prepare_viral_features(
    df: pd.DataFrame, viral_threshold_quantile=0.80
) -> pd.DataFrame:
    """
    Prepares features for viral prediction.
    Includes:
    - View Velocity Calculation (Target)
    - Engagement Velocities
    - Initial Virality Slope
    - Interaction Density
    - Static Features (Time, Text)
    """

    # 1. Static Features (Time & Text)
    df = temporal_features.add_date_features(df, "published_at")

    if "title" in df.columns:
        try:
            df = text_features.extract_title_features(df, title_col="title")
        except Exception:
            # Fallback if text_features fails
            df["title"] = df["title"].fillna("")
            df["title_len"] = df["title"].str.len()
            df["caps_ratio"] = 0
            df["has_digits"] = 0
    else:
        df["title_len"] = 0
        df["caps_ratio"] = 0
        df["has_digits"] = 0

    features_list = []
    all_velocities = []

    # First pass: Calculate View Velocities for Labeling
    for vid, group in df.groupby("video_id"):
        if len(group) < 2:
            continue

        group = group.sort_values("stat_time")

        start_time = group["stat_time"].iloc[0]
        end_time = group["stat_time"].iloc[-1]
        time_diff_hours = (end_time - start_time).total_seconds() / 3600.0

        if time_diff_hours < 2:
            continue

        view_diff = group["views"].iloc[-1] - group["views"].iloc[0]
        view_velocity = view_diff / (time_diff_hours + 0.1)
        all_velocities.append(view_velocity)

    if not all_velocities:
        raise ValueError("No valid videos with sufficient time span found.")

    # Define "viral" threshold
    viral_threshold = pd.Series(all_velocities).quantile(viral_threshold_quantile)

    # Second pass: Build Features
    for vid, group in df.groupby("video_id"):
        if len(group) < 2:
            continue

        group = group.sort_values("stat_time")
        start_row = group.iloc[0]
        end_row = group.iloc[-1]

        delta = end_row["stat_time"] - start_row["stat_time"]
        time_diff_hours = delta.total_seconds() / 3600.0

        if time_diff_hours < 2:
            continue

        # Core metrics
        start_views = start_row["views"]
        end_views = end_row["views"]
        view_diff = end_views - start_views
        view_velocity = view_diff / (time_diff_hours + 0.1)

        # Engagement Velocities
        denom = time_diff_hours + 0.1
        like_velocity = (end_row["likes"] - start_row["likes"]) / denom
        comment_velocity = (end_row["comments"] - start_row["comments"]) / denom

        # Engagement ratios
        like_ratio = end_row["likes"] / (end_views + 1)
        comment_ratio = end_row["comments"] / (end_views + 1)

        # Age
        delta_age = start_row["stat_time"] - start_row["published_at"]
        video_age_hours = max(0.5, delta_age.total_seconds() / 3600.0)

        # Advanced Features
        initial_virality_slope = np.log1p(start_views) / np.log1p(video_age_hours)

        interaction_num = np.log1p(start_row["likes"] + start_row["comments"] * 2)
        interaction_den = np.log1p(start_views + 1)
        interaction_density = interaction_num / interaction_den

        # Label
        is_viral = 1 if view_velocity >= viral_threshold else 0

        features_list.append(
            {
                "is_viral": is_viral,
                "like_velocity": like_velocity,
                "comment_velocity": comment_velocity,
                "start_views": start_views,
                "log_start_views": np.log1p(start_views),
                "like_ratio": like_ratio,
                "comment_ratio": comment_ratio,
                "video_age_hours": video_age_hours,
                "duration_seconds": start_row.get("duration_seconds", 0),
                "hours_tracked": time_diff_hours,
                "snapshots": len(group),
                "initial_virality_slope": initial_virality_slope,
                "interaction_density": interaction_density,
                "hour_sin": start_row.get("hour_sin", 0),
                "hour_cos": start_row.get("hour_cos", 0),
                "title_len": start_row.get("title_len", 0),
                "caps_ratio": start_row.get("caps_ratio", 0),
                "has_digits": start_row.get("has_digits", 0),
            }
        )

    return pd.DataFrame(features_list)
