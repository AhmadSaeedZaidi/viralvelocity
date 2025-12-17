import pandas as pd
import streamlit as st
from utils.api_client import YoutubeMLClient

def render():
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
            width='stretch'
        )
        
        st.info(
            "Note: 'Mock' backend means the API is running in simulation mode because "
            "real weights haven't been uploaded yet."
        )
    else:
        st.warning("Could not fetch model status.")

if __name__ == "__main__":
    render()