import numpy as np
import plotly.figure_factory as ff
import streamlit as st
from utils.db_client import DatabaseClient


def render():
    st.title("Data Drift Monitor")
    st.markdown(
        "Detect distribution shifts between **Training Data** (Historical) and "
        "**Live Production Data** (Last 24h)."
    )

    try:
        db = DatabaseClient()
        ref_df = db.get_training_data_distribution()
        curr_df = db.get_live_data_distribution()
    except Exception as e:
        st.error(f"Failed to connect to database: {e}")
        return

    if ref_df.empty or curr_df.empty:
        st.warning(
            "Not enough data in database to perform drift analysis.\n\n"
            f"- **Training Data (Older than 24h):** {len(ref_df)} records\n"
            f"- **Live Data (Last 24h):** {len(curr_df)} records"
        )
        return

    # Feature Selector
    feature_map = {
        "View Count Distribution": "views",
        "Like Count Distribution": "likes",
        "Comment Count Distribution": "comments",
        "Video Duration": "duration_seconds",
    }

    feature_label = st.selectbox("Select Feature to Monitor", list(feature_map.keys()))
    feature_col = feature_map[feature_label]

    # Time Range (Visual only for now, query is fixed)
    # days = st.slider("Lookback Window (Days)", 7, 90, 30)

    st.divider()

    # --- Real Data Logic ---

    # Clean data (drop NaNs)
    ref_data = ref_df[feature_col].dropna().values
    curr_data = curr_df[feature_col].dropna().values

    # Log transform for skewed metrics (views, likes)
    if feature_col in ["views", "likes", "comments"]:
        ref_data = np.log1p(ref_data)
        curr_data = np.log1p(curr_data)
        st.caption("Note: Data is Log-Transformed (log1p) for better visualization.")

    # --- Visualization: Distribution Plot ---
    st.subheader(f"Distribution Comparison: {feature_label}")

    if len(ref_data) > 0 and len(curr_data) > 0:
        hist_data = [ref_data, curr_data]
        group_labels = ["Training Data (Reference)", "Live Data (Current)"]
        colors = ["#3339FF", "#FF3333"]

        fig_dist = ff.create_distplot(
            hist_data,
            group_labels,
            bin_size=(
                max(ref_data.max(), curr_data.max())
                - min(ref_data.min(), curr_data.min())
            )
            / 20,
            colors=colors,
            show_rug=False,
        )

        x_label = (
            f"{feature_label} (Log Scale)"
            if feature_col in ["views", "likes", "comments"]
            else feature_label
        )
        fig_dist.update_layout(
            title_text="Distribution Shift Detected",
            xaxis_title=x_label,
            yaxis_title="Density",
        )
        st.plotly_chart(fig_dist, use_container_width=True)

        # --- Statistical Test (KS Test Simulation) ---
        st.subheader("Statistical Drift Metrics")

        col1, col2, col3 = st.columns(3)

        # Kolmogorov-Smirnov Statistic
        from scipy.stats import ks_2samp

        ks_stat, p_value = ks_2samp(ref_data, curr_data)
        is_significant = p_value < 0.05
        is_meaningful = ks_stat > 0.1
        drift_detected = is_significant and is_meaningful

        with col1:
            st.metric(
                "KS Statistic",
                f"{ks_stat:.3f}",
                delta="Drift Detected" if drift_detected else "Stable",
                delta_color="inverse",
            )
            st.caption(f"P-Value: {p_value:.4f}")

        with col2:
            st.metric("Reference Mean (Log)", f"{np.mean(ref_data):.2f}")

        with col3:
            st.metric(
                "Current Mean (Log)",
                f"{np.mean(curr_data):.2f}",
                delta=f"{np.mean(curr_data) - np.mean(ref_data):.2f}",
            )

        if drift_detected:
            st.error(
                "🚨 **Drift Alert:** The live data distribution has significantly "
                "deviated from the training set. Recommended Action: **Retrain Model**."
            )
        elif is_significant:
            st.warning(
                "⚠️ **Minor Shift:** Statistically significant difference detected, "
                "but magnitude is small. Monitor closely."
            )
        else:
            st.success("✅ **Stable:** Data distribution is within expected bounds.")
    else:
        st.warning("Insufficient data points for distribution plot.")

    st.divider()

    # --- Timeline View (Placeholder for now as we need time-series aggregation) ---
    # st.subheader("Drift Over Time")
    # ... (Requires more complex query to get drift over time)


if __name__ == "__main__":
    render()
