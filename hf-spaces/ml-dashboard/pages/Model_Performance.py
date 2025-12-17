import pandas as pd
import streamlit as st
import plotly.express as px
from utils.data_processing import calculate_mape, format_large_number
from utils.visualizations import plot_accuracy_metric, plot_dummy_drift
from utils.db_client import DatabaseClient

def render():
    st.title("📈 Model Performance Monitoring")

    # st.warning("⚠️ Displaying simulated metrics. Connect to Neon DB for real-time history.")
    
    try:
        db = DatabaseClient()
        # Fetch real data (limit to 100 for performance demo)
        df = db.get_video_stats(limit=100)
    except Exception as e:
        st.error(f"Database Connection Error: {e}")
        return

    if df.empty:
        st.warning("No data found in database.")
        return

    # --- 1. Velocity Model Performance (Simulated vs Real) ---
    # Since we don't have "Predicted" vs "Actual" stored in the DB yet (unless we log predictions),
    # we will simulate the "Prediction" based on a simple heuristic to show the chart working with real "Actuals".
    
    # Real Actuals
    y_true = df['views']
    
    # Simulated Predictions (e.g., +/- 20% error)
    import numpy as np
    noise = np.random.normal(1, 0.2, size=len(y_true))
    y_pred = y_true * noise
    
    velocity_mape = calculate_mape(y_true, y_pred)

    # Top Level Metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.plotly_chart(
            plot_accuracy_metric("Velocity MAPE (Simulated Preds)", velocity_mape, 15.0),
            use_container_width=True,
        )
    with col2:
        st.plotly_chart(
            plot_accuracy_metric("Clickbait F1 (Static)", 0.88, 0.85),
            use_container_width=True,
        )
    with col3:
        st.plotly_chart(
            plot_accuracy_metric("Genre Accuracy (Static)", 0.92, 0.91),
            use_container_width=True,
        )

    st.divider()

    # --- Data Volume ---
    st.subheader("Data Volume Analysis")

    # Show total predictions using formatter
    total_rows = len(df)
    st.metric("Total Video Stats Analyzed", format_large_number(total_rows))

    # Simple Time Series of Views
    fig = px.line(df, x='time', y='views', title='Recent View Counts (Real Data)')
    st.plotly_chart(fig, use_container_width=True)

    st.write(
        """
    **Note:** Currently, the database stores *actual* video statistics. 
    To calculate real model performance, we need to log model predictions to a separate table 
    and join them with these actuals.
    """
    )

if __name__ == "__main__":
    render()