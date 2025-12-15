import os

import pandas as pd
from sqlalchemy import create_engine, text


class DataLoader:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        
        if not self.db_url:
            raise ValueError(
                "DATABASE_URL environment variable is not set. "
                "Ensure it's configured in GitHub Secrets and passed to the workflow."
            )
        
        # SQLAlchemy 1.4+ requires postgresql:// instead of postgres://
        if self.db_url.startswith("postgres://"):
            self.db_url = self.db_url.replace("postgres://", "postgresql://", 1)
        
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

    def get_viral_training_data(self):
        """
        Fetch video discovery + stats for viral prediction.
        Uses search_discovery (more data) joined with video_stats time series.
        """
        query = text("""
            WITH discovered_videos AS (
                -- Get all videos found via search (more entries than trending)
                SELECT DISTINCT video_id, MIN(discovered_at) as first_discovered
                FROM search_discovery
                GROUP BY video_id
            ),
            video_stats_series AS (
                -- Get stats time series for discovered videos
                SELECT 
                    s.video_id,
                    s.time,
                    s.views,
                    s.likes,
                    s.comments
                FROM video_stats s
                INNER JOIN discovered_videos d ON s.video_id = d.video_id
            )
            SELECT 
                v.video_id,
                v.title,
                v.duration_seconds,
                v.published_at,
                d.first_discovered,
                s.time as stat_time,
                s.views,
                s.likes,
                s.comments
            FROM discovered_videos d
            JOIN videos v ON d.video_id = v.video_id
            JOIN video_stats_series s ON d.video_id = s.video_id
            ORDER BY d.video_id, s.time ASC
        """)
        return pd.read_sql(query, self.engine)

    def get_training_pairs(self, target_hours=168, window_hours=24):
        """
        Get training pairs: earliest stats (T=0) and target stats (T=target_hours).
        
        Args:
            target_hours: Target time after publish (default 168 = 7 days)
            window_hours: Flexibility window around target (default ±24 hours)
        """
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
                    v.published_at + INTERVAL '{target_hours - window_hours} hours'
                ) AND (
                    v.published_at + INTERVAL '{target_hours + window_hours} hours'
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

    def get_training_pairs_flexible(self):
        """
        Fallback: Get training pairs using earliest and latest stats for each video.
        Use when strict time windows return no data.
        """
        query = text("""
            WITH 
            earliest_stats AS (
                SELECT DISTINCT ON (video_id) video_id, views as start_views, 
                likes as start_likes, comments as start_comments, time as start_time
                FROM video_stats 
                ORDER BY video_id, time ASC
            ),
            latest_stats AS (
                SELECT DISTINCT ON (video_id) video_id, views as target_views, 
                time as target_time
                FROM video_stats 
                ORDER BY video_id, time DESC
            )
            SELECT v.video_id, v.title, v.duration_seconds, v.published_at, v.channel_id,
                e.start_views, e.start_likes, e.start_comments, 
                l.target_views,
                EXTRACT(EPOCH FROM (l.target_time - e.start_time))/3600 as hours_between
            FROM videos v
            JOIN earliest_stats e ON v.video_id = e.video_id
            JOIN latest_stats l ON v.video_id = l.video_id
            WHERE l.target_time > e.start_time + INTERVAL '1 hour'
        """)
        return pd.read_sql(query, self.engine)