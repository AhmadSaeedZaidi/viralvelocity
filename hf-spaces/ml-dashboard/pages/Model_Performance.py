import pandas as pd
import streamlit as st
from utils.data_processing import calculate_mape, format_large_number
from utils.visualizations import plot_accuracy_metric, plot_dummy_drift

st.set_page_config(page_title="Model Performance", page_icon="📈")

st.title("📈 Model Performance Monitoring")

st.warning("⚠️ Displaying simulated metrics. Connect to Neon DB for real-time history.")

# Simulate data for Velocity Model to demonstrate utility usage
y_true = pd.Series([1000, 5000, 10000, 2000, 8000])
y_pred = pd.Series([1100, 4800, 10500, 2100, 7500])
velocity_mape = calculate_mape(y_true, y_pred)

# Top Level Metrics
col1, col2, col3 = st.columns(3)
with col1:
    st.plotly_chart(
        plot_accuracy_metric("Velocity MAPE", velocity_mape, 15.0),
        use_container_width=True,
    )
with col2:
    st.plotly_chart(
        plot_accuracy_metric("Clickbait F1", 0.88, 0.85),
        use_container_width=True,
    )
with col3:
    st.plotly_chart(
        plot_accuracy_metric("Genre Accuracy", 0.92, 0.91),
        use_container_width=True,
    )

st.divider()

# Drift Analysis
st.subheader("Drift Detection")

# Show total predictions using formatter
total_preds = 15420
st.metric("Total Predictions Analyzed", format_large_number(total_preds))

st.plotly_chart(plot_dummy_drift(), use_container_width=True)

st.write(
    """
**Interpretation:** The drift score represents the divergence between training data
distribution and live production data. A spike above 0.8 indicates retrain required.
"""
)