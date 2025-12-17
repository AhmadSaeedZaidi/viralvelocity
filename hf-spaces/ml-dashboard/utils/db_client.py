import os
import pandas as pd
from sqlalchemy import create_engine, text
import streamlit as st

class DatabaseClient:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable is not set.")
        self.engine = create_engine(self.db_url)

    @st.cache_data(ttl=300)  # Cache results for 5 minutes
    def get_video_stats(_self, limit=1000):
        """Fetch recent video statistics."""
        query = text("""
            SELECT 
                v.video_id,
                v.title,
                v.duration_seconds,
                vs.views,
                vs.likes,
                vs.comments,
                vs.time
            FROM video_stats vs
            JOIN videos v ON vs.video_id = v.video_id
            ORDER BY vs.time DESC
            LIMIT :limit
        """)
        with _self.engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"limit": limit})
        return df

    @st.cache_data(ttl=3600)  # Cache for 1 hour
    def get_training_data_distribution(_self):
        """
        Fetch a sample of historical data to serve as a baseline/reference.
        In a real scenario, this might come from a separate 'training_set' table
        or a specific time range in the past.
        """
        # For now, we'll just take older data as "reference"
        query = text("""
            SELECT 
                vs.views,
                vs.likes,
                vs.comments,
                v.duration_seconds
            FROM video_stats vs
            JOIN videos v ON vs.video_id = v.video_id
            WHERE vs.time < NOW() - INTERVAL '7 days'
            LIMIT 5000
        """)
        with _self.engine.connect() as conn:
            df = pd.read_sql(query, conn)
        return df

    @st.cache_data(ttl=300)
    def get_live_data_distribution(_self):
        """Fetch recent data to compare against baseline."""
        query = text("""
            SELECT 
                vs.views,
                vs.likes,
                vs.comments,
                v.duration_seconds
            FROM video_stats vs
            JOIN videos v ON vs.video_id = v.video_id
            WHERE vs.time >= NOW() - INTERVAL '24 hours'
            LIMIT 2000
        """)
        with _self.engine.connect() as conn:
            df = pd.read_sql(query, conn)
        return df
