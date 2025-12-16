import streamlit as st
from utils.api_client import get_model_metrics, get_model_list
from pages import Drift_Detection, Feature_Analysis, Live_Predictions, Model_Configs, Model_Performance

st.set_page_config(
    page_title="ML Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.sidebar.title("ML Dashboard Navigation")
page = st.sidebar.radio(
    "Go to:",
    (
        "Live Predictions",
        "Model Performance",
        "Feature Analysis",
        "Drift Detection",
        "Model Configs"
    )
)

if page == "Live Predictions":
    Live_Predictions.render()
elif page == "Model Performance":
    Model_Performance.render()
elif page == "Feature Analysis":
    Feature_Analysis.render()
elif page == "Drift Detection":
    Drift_Detection.render()
elif page == "Model Configs":
    Model_Configs.render()

st.sidebar.markdown("---")
st.sidebar.info(
    "Built with ❤️ using Streamlit. Backend powered by FastAPI and Hugging Face Hub."
)
