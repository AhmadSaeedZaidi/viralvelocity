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

    st.divider()

    # --- Advanced Model Stats (Simulated based on Training Pipelines) ---
    st.subheader("Training Pipeline Metrics (Simulated)")
    
    tab1, tab2, tab3 = st.tabs(["Velocity (Regression)", "Clickbait (Classification)", "Genre (Multi-class)"])
    
    with tab1:
        st.markdown("### Velocity Predictor Performance")
        # Scatter plot: Actual vs Predicted
        fig_scatter = px.scatter(
            x=y_true, y=y_pred, 
            labels={'x': 'Actual Views', 'y': 'Predicted Views'},
            title="Actual vs Predicted Views (Log Scale)",
            log_x=True, log_y=True,
            trendline="ols"
        )
        st.plotly_chart(fig_scatter, use_container_width=True)
        
        # Residual Plot
        residuals = y_true - y_pred
        fig_resid = px.histogram(
            residuals, nbins=30, 
            title="Residual Distribution (Errors)",
            labels={'value': 'Residual (Actual - Predicted)'}
        )
        st.plotly_chart(fig_resid, use_container_width=True)

    with tab2:
        st.markdown("### Clickbait Detector Performance")
        # Confusion Matrix (Simulated)
        cm_data = [[85, 15], [10, 90]] # TP, FP, FN, TN
        fig_cm = px.imshow(
            cm_data,
            labels=dict(x="Predicted", y="Actual", color="Count"),
            x=['Solid', 'Clickbait'],
            y=['Solid', 'Clickbait'],
            text_auto=True,
            title="Confusion Matrix"
        )
        st.plotly_chart(fig_cm, use_container_width=True)
        
        # ROC Curve (Simulated)
        fpr = [0, 0.1, 0.2, 0.5, 0.8, 1]
        tpr = [0, 0.8, 0.9, 0.95, 0.98, 1]
        fig_roc = px.area(
            x=fpr, y=tpr,
            title=f"ROC Curve (AUC = 0.92)",
            labels=dict(x='False Positive Rate', y='True Positive Rate'),
        )
        fig_roc.add_shape(
            type='line', line=dict(dash='dash'),
            x0=0, x1=1, y0=0, y1=1
        )
        st.plotly_chart(fig_roc, use_container_width=True)

    with tab3:
        st.markdown("### Genre Classifier Performance")
        # Class-wise F1 Scores
        genres = ['Gaming', 'Tech', 'Vlog', 'Education', 'Music']
        f1_scores = [0.95, 0.88, 0.82, 0.91, 0.85]
        
        fig_bar = px.bar(
            x=genres, y=f1_scores,
            title="F1 Score by Genre",
            labels={'x': 'Genre', 'y': 'F1 Score'},
            color=f1_scores,
            color_continuous_scale='Viridis'
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.write(
        """
    **Note:** Currently, the database stores *actual* video statistics. 
    To calculate real model performance, we need to log model predictions to a separate table 
    and join them with these actuals.
    """
    )

if __name__ == "__main__":
    render()