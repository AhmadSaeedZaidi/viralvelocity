from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.sql import func

from collector.database import Base, engine


class VideoStat(Base):
    """
    Represents a snapshot of a video's performance at a specific time.
    Updated to include Rich Metadata for ML Clustering and Regression.
    """
    __tablename__ = "video_stats"

    # --- Composite Primary Key ---
    time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    video_id = Column(String, nullable=False)
    
    # --- Statistics (Target Variables) ---
    views = Column(BigInteger, nullable=False)
    likes = Column(BigInteger, nullable=False)
    comments = Column(BigInteger, nullable=False)
    
    # --- Text Metadata (NLP Features) ---
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    
    # --- Rich Metadata (New ML Features) ---
    category_id = Column(String, nullable=True)      
    duration_seconds = Column(Integer, nullable=True)
    definition = Column(String, nullable=True)     
    published_at = Column(DateTime(timezone=True), nullable=True) 
    
    # --- UI Assets ---
    thumbnail_url = Column(String, nullable=True)

    # Explicit Primary Key Constraint
    __table_args__ = (
        PrimaryKeyConstraint('video_id', 'time'),
    )

    @property
    def video_link(self):
        """
        Generates the YouTube link on the fly.
        """
        return f"https://www.youtube.com/watch?v={self.video_id}"

def init_db():
    """
    Creates tables, enables Hypertable, and sets Retention Policy.
    """
    try:
        # 1. Create standard table schema
        Base.metadata.create_all(bind=engine)
        
        with engine.connect() as conn:
            # 2. Enable TimescaleDB Hypertable
            conn.execute(text(("SELECT create_hypertable('video_stats', "
                               "'time', if_not_exists => TRUE);")))
            
            # 3. Enable Auto-Cleanup (Retention Policy)
            # This deletes data older than 30 days automatically
            conn.execute(text(("SELECT add_retention_policy('video_stats', "
                               "INTERVAL '30 days', if_not_exists => TRUE);")))
            
            conn.commit()
            print("Database initialized: Hypertable + Retention Policy active.")
            
    except Exception as e:
        print(f"Database Init Error: {e}")