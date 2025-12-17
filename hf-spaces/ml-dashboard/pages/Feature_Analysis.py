import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from utils.api_client import YoutubeMLClient
from utils.db_client import DatabaseClient


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
        st.plotly_chart(fig_imp, width="stretch")

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

    try:
        db = DatabaseClient()
        df_data = db.get_training_data_distribution()

        if not df_data.empty:
            st.write("Analyze how input features interact with each other.")

            # Calculate basic features to match some model inputs
            # Avoid log(0)
            df_data["log_views"] = np.log1p(df_data["views"])
            df_data["log_likes"] = np.log1p(df_data["likes"])
            df_data["log_comments"] = np.log1p(df_data["comments"])
            df_data["log_duration"] = np.log1p(df_data["duration_seconds"])

            # Select numeric columns relevant for analysis
            cols_to_corr = [
                "views",
                "likes",
                "comments",
                "duration_seconds",
                "log_views",
                "log_likes",
                "log_comments",
                "log_duration",
            ]

            # Filter columns that exist
            cols_to_corr = [c for c in cols_to_corr if c in df_data.columns]

            numeric_df = df_data[cols_to_corr]

            # Calculate correlation
            corr_matrix = numeric_df.corr()

            fig_corr = go.Figure(
                data=go.Heatmap(
                    z=corr_matrix.values,
                    x=corr_matrix.columns,
                    y=corr_matrix.columns,
                    colorscale="RdBu",
                    zmin=-1,
                    zmax=1,
                )
            )
            fig_corr.update_layout(xaxis_title="Features", yaxis_title="Features")
            st.plotly_chart(fig_corr, width="stretch")
        else:
            st.warning("Insufficient data for correlation analysis.")

    except Exception as e:
        st.error(f"Could not calculate correlations: {e}")


if __name__ == "__main__":
    render()
