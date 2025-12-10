from sqlalchemy import (
    Column, 
    String, 
    BigInteger, 
    DateTime, 
    PrimaryKeyConstraint, 
    Text, 
    Integer, 
    Boolean,
    text,
    ForeignKey
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from collector.database import Base, engine

# --- 1. The Core Video Table (Metadata) ---
# Stores static or slowly-changing data. Created when we first find the video.
class Video(Base):
    __tablename__ = "videos"

    video_id = Column(String, primary_key=True)
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    channel_id = Column(String, nullable=True)
    category_id = Column(String, nullable=True)
    
    # Technical Specs
    duration_seconds = Column(Integer, nullable=True)
    definition = Column(String, nullable=True)
    
    # Static Content Features
    made_for_kids = Column(Boolean, nullable=True)
    audio_language = Column(String, nullable=True)
    
    # Assets
    thumbnail_url = Column(String, nullable=True)
    
    # System Metadata
    first_seen_at = Column(DateTime(timezone=True), default=func.now())

# --- 2. The Statistics Table (Time Series) ---
class VideoStat(Base):
    __tablename__ = "video_stats"

    # Composite PK: (video_id, time)
    time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    
    # The Numbers
    views = Column(BigInteger, nullable=False)
    likes = Column(BigInteger, nullable=False)
    comments = Column(BigInteger, nullable=False)
    
    __table_args__ = (
        PrimaryKeyConstraint('video_id', 'time'),
    )

# --- 3. Discovery Logs ---
# Tracks HOW we found the video (Search vs Trending)
class SearchDiscovery(Base):
    __tablename__ = "search_discovery"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    query = Column(String, nullable=False)
    discovered_at = Column(DateTime(timezone=True), default=func.now(), index=True)

class TrendingDiscovery(Base):
    __tablename__ = "trending_discovery"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    discovered_at = Column(DateTime(timezone=True), default=func.now(), index=True)

def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT create_hypertable('video_stats', 'time', if_not_exists => TRUE);"))
            # Retention: Keep raw stats for 60 Days
            conn.execute(text("SELECT add_retention_policy('video_stats', INTERVAL '60 days', if_not_exists => TRUE);"))
            conn.commit()
            print("Database initialized: Normalized Schema + Hypertable active.")
    except Exception as e:
        print(f"Database Init Info: {e}")