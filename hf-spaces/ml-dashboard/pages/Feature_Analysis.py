import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

def render():
    st.title("Global Feature Analysis")
    st.markdown("Understand which input variables drive your model predictions.")

    # Model Selector
    model_choice = st.selectbox(
        "Select Model to Analyze",
        [
            "Velocity Predictor",
            "Clickbait Detector",
            "Viral Trend Classifier",
            "Anomaly Detector",
        ],
    )

    st.divider()

    # --- Section 1: Feature Importance ---
    st.subheader(f"Feature Importance: {model_choice}")

    # Simulated Importance Data based on the model choice
    if model_choice == "Velocity Predictor":
        features = {
            "slope_views_24h": 0.45,
            "channel_avg_views": 0.25,
            "slope_engagement": 0.15,
            "duration_seconds": 0.10,
            "published_hour": 0.05
        }
    elif model_choice == "Clickbait Detector":
        features = {
            "engagement_ratio": 0.60,
            "title_caps_ratio": 0.20,
            "view_count": 0.15,
            "video_duration": 0.05
        }
    else:
        features = {"Feature_A": 0.4, "Feature_B": 0.3, "Feature_C": 0.2, "Feature_D": 0.1}

    # Create Dataframe
    df_imp = pd.DataFrame(list(features.items()), columns=["Feature", "Importance"])
    df_imp = df_imp.sort_values(by="Importance", ascending=True)

    # Plot
    fig_imp = px.bar(
        df_imp, x="Importance", y="Feature", orientation='h',
        title="Global Feature Importance (SHAP Approximation)",
        color="Importance", color_continuous_scale="Viridis",
        labels={'Importance': 'Importance Score', 'Feature': 'Feature Name'}
    )
    st.plotly_chart(fig_imp, use_container_width=True)

    st.info(
        "💡 **Insight:** 'Slope Views' is the dominant predictor for Velocity, "
        "confirming that early traction dictates long-term success."
    )

    st.divider()

    # --- Section 2: Correlation Matrix ---
    st.subheader("Feature Correlation Heatmap")
    st.write("Analyze how input features interact with each other.")

    # Generate dummy correlation matrix
    cols = list(features.keys())
    data = np.random.rand(len(cols), len(cols))
    # Make it symmetric for a valid correlation matrix
    corr_matrix = (data + data.T) / 2
    np.fill_diagonal(corr_matrix, 1.0)

    fig_corr = go.Figure(data=go.Heatmap(
        z=corr_matrix,
        x=cols,
        y=cols,
        colorscale='RdBu',
        zmin=-1, zmax=1
    ))
    fig_corr.update_layout(
        xaxis_title="Features",
        yaxis_title="Features"
    )
    st.plotly_chart(fig_corr, use_container_width=True)

if __name__ == "__main__":
    render()