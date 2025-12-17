import numpy as np
import pandas as pd
import plotly.express as px
import plotly.figure_factory as ff
import streamlit as st

def render():
    st.title("📉 Data Drift Monitor")
    st.markdown(
        "Detect distribution shifts between **Training Data** and "
        "**Live Production Data**."
    )

    # Feature Selector
    feature = st.selectbox(
        "Select Feature to Monitor",
        ["View Count Distribution", "Engagement Score", "Title Length", "Video Duration"]
    )

    # Time Range
    days = st.slider("Lookback Window (Days)", 7, 90, 30)

    st.divider()

    # --- Simulation Logic ---
    # In a real app, you would query your Neon DB here.
    # For now, we simulate a "Drift" scenario where production data has shifted.

    np.random.seed(42)

    # Generate Reference Data (Training) - Normal Distribution
    ref_data = np.random.normal(loc=50, scale=10, size=1000)

    # Generate Current Data (Production) - Shifted Mean (Drift!)
    curr_data = np.random.normal(loc=55, scale=12, size=500)

    # --- Visualization: Distribution Plot ---
    st.subheader(f"Distribution Comparison: {feature}")

    hist_data = [ref_data, curr_data]
    group_labels = ['Training Data (Reference)', 'Live Data (Current)']
    colors = ['#3339FF', '#FF3333']

    fig_dist = ff.create_distplot(
        hist_data, group_labels, bin_size=2, 
        colors=colors, show_rug=False
    )
    fig_dist.update_layout(title_text="Distribution Shift Detected")
    st.plotly_chart(fig_dist, use_container_width=True)

    # --- Statistical Test (KS Test Simulation) ---
    st.subheader("Statistical Drift Metrics")

    col1, col2, col3 = st.columns(3)

    # Kolmogorov-Smirnov Statistic (Distance between distributions)
    # 0.0 = Identical, 1.0 = Completely different
    ks_stat = 0.15 
    drift_detected = ks_stat > 0.10

    with col1:
        st.metric(
            "KS Statistic", 
            f"{ks_stat:.3f}", 
            delta="Drift Detected" if drift_detected else "Stable",
            delta_color="inverse"
        )

    with col2:
        st.metric("Reference Mean", f"{np.mean(ref_data):.2f}")

    with col3:
        st.metric(
            "Current Mean", 
            f"{np.mean(curr_data):.2f}", 
            delta=f"{np.mean(curr_data) - np.mean(ref_data):.2f}"
        )

        if drift_detected:
            st.error(
                "🚨 **Drift Alert:** The live data distribution has significantly "
                "deviated from the training set. Recommended Action: **Retrain Model**."
            )
        else:
            st.success(
                "✅ **Stable:** Data distribution is within expected bounds."
            )

    st.divider()

    # --- Timeline View ---
    st.subheader("Drift Over Time")
    dates = pd.date_range(end=pd.Timestamp.today(), periods=days)
    drift_scores = np.linspace(0.02, 0.15, days) + np.random.normal(0, 0.01, days)

    df_timeline = pd.DataFrame({"Date": dates, "Drift Score": drift_scores})

    fig_timeline = px.line(
        df_timeline, x="Date", y="Drift Score",
        title="Drift Score Trend (Last 30 Days)",
        markers=True
    )
    # Add threshold line
    fig_timeline.add_hline(
        y=0.10, line_dash="dash", line_color="red", 
        annotation_text="Drift Threshold"
    )
    st.plotly_chart(fig_timeline, use_container_width=True)

if __name__ == "__main__":
    render()

fig_line = px.line(
    df_timeline,
    x="Date",
    y="Drift Score",
    title="KS Statistic Trend (30 Days)",
)
fig_line.add_hline(
    y=0.10, line_dash="dash", line_color="red", annotation_text="Threshold"
)

st.plotly_chart(fig_line, use_container_width=True)