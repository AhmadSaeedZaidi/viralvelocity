import os

import pandas as pd
from sqlalchemy import create_engine, text


class DataLoader:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        self.engine = create_engine(self.db_url)

    def get_video_metadata(self):
        query = (
            "SELECT video_id, title, tags, duration_seconds, published_at "
            "FROM videos"
        )
        return pd.read_sql(query, self.engine)

    def get_joined_data(self):
        # Helper for static models
        meta = self.get_video_metadata()
        stats = pd.read_sql(
            "SELECT DISTINCT ON (video_id) * FROM video_stats "
            "ORDER BY video_id, time DESC",
            self.engine
        )
        return pd.merge(meta, stats, on='video_id', how='inner')
    
    def get_latest_stats(self):
        return pd.read_sql(
            "SELECT DISTINCT ON (video_id) * FROM video_stats "
            "ORDER BY video_id, time DESC",
            self.engine
        )

    def get_trending_history(self):
        return pd.read_sql(
            "SELECT * FROM trending_discovery "
            "ORDER BY video_id, discovered_at ASC",
            self.engine
        )

    def get_training_pairs(self, target_hours=168):
        # 1. Finds 'Earliest' row (T0)
        # 2. Finds 'Target' row (Tx) close to published_at + target_hours
        # 3. Joins them
        query = text(
            f"""
            WITH 
            earliest_stats AS (
                SELECT DISTINCT ON (video_id) video_id, views as start_views, 
                likes as start_likes, comments as start_comments, time as start_time
                FROM video_stats 
                ORDER BY video_id, time ASC
            ),
            target_stats AS (
                SELECT DISTINCT ON (video_id) s.video_id, s.views as target_views, 
                s.time as target_time
                FROM video_stats s 
                JOIN videos v ON s.video_id = v.video_id
                WHERE s.time BETWEEN (
                    v.published_at + INTERVAL '{target_hours - 12} hours'
                ) AND (
                    v.published_at + INTERVAL '{target_hours + 12} hours'
                )
                ORDER BY s.video_id, s.time DESC
            )
         SELECT v.video_id, v.title, v.duration_seconds, v.published_at, v.channel_id,
             e.start_views, e.start_likes, e.start_comments, t.target_views
            FROM videos v
            JOIN earliest_stats e ON v.video_id = e.video_id
            JOIN target_stats t ON v.video_id = t.video_id
            """
        )
        return pd.read_sql(query, self.engine)