import pandas as pd
import streamlit as st
from utils.api_client import YoutubeMLClient

st.set_page_config(page_title="System Config", page_icon="🔧")
client = YoutubeMLClient()

st.title("🔧 System Configuration")

st.subheader("Microservice Status")
health = client.get_health()
st.json(health)

st.divider()

st.subheader("Model Registry Status")
status = client.get_model_status()

if status:
    # Convert nested JSON to DataFrame for nice table
    df = pd.DataFrame.from_dict(status, orient='index')
    st.dataframe(
        df,
        column_config={
            "loaded": st.column_config.CheckboxColumn("Memory Loaded"),
            "type": "Model Class",
            "backend": "Backend Engine"
        },
        use_container_width=True
    )
    
    st.info(
        "Note: 'Mock' backend means the API is running in simulation mode because "
        "real weights haven't been uploaded yet."
    )
else:
    st.warning("Could not fetch model status.")