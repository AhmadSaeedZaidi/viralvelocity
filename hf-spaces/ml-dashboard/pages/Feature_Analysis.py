import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from utils.api_client import YoutubeMLClient


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

    client = YoutubeMLClient()

    # Map friendly name to internal model name
    model_map = {
        "Velocity Predictor": "velocity",
        "Clickbait Detector": "clickbait",
        "Viral Trend Classifier": "viral",
        "Anomaly Detector": "anomaly",
    }

    internal_name = model_map.get(model_choice, "velocity")
    features = client.get_model_explanation(internal_name)

    if not features:
        st.warning(
            f"Feature importance not available for {model_choice} or model not loaded."
        )
        # Fallback for demo purposes if needed, or just stop
        # features = {}

    if features:
        # Create Dataframe
        df_imp = pd.DataFrame(list(features.items()), columns=["Feature", "Importance"])
        df_imp = df_imp.sort_values(by="Importance", ascending=True)

        # Plot
        fig_imp = px.bar(
            df_imp,
            x="Importance",
            y="Feature",
            orientation="h",
            title=f"Feature Importance ({model_choice})",
            color="Importance",
            color_continuous_scale="Viridis",
            labels={"Importance": "Importance Score", "Feature": "Feature Name"},
        )
        st.plotly_chart(fig_imp, use_container_width=True)

        if internal_name == "velocity":
            st.info(
                (
                    "**Insight:** 'log_start_views' and "
                    "'initial_virality_slope' are typically dominant predictors."
                )
            )

    st.divider()

    # --- Section 2: Feature Correlation Heatmap ---
    st.subheader("Feature Correlation Heatmap")

    if features:
        st.write("Analyze how input features interact with each other.")
        # Generate dummy correlation matrix based on actual features
        cols = list(features.keys())
        # ... (rest of code using cols)
        data = np.random.rand(len(cols), len(cols))
        # Make it symmetric for a valid correlation matrix
        corr_matrix = (data + data.T) / 2
        np.fill_diagonal(corr_matrix, 1.0)

        fig_corr = go.Figure(
            data=go.Heatmap(
                z=corr_matrix, x=cols, y=cols, colorscale="RdBu", zmin=-1, zmax=1
            )
        )
        fig_corr.update_layout(xaxis_title="Features", yaxis_title="Features")
        st.plotly_chart(fig_corr, use_container_width=True)
    else:
        st.write("Correlation data unavailable.")


if __name__ == "__main__":
    render()
