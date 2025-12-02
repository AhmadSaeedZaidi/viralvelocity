from database import Base, engine
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.sql import func


class VideoStat(Base):
    """
    Represents a snapshot of a video's performance at a specific time.
    """
    __tablename__ = "video_stats"

    # Composite Primary Key: Video ID + Timestamp (Required for TimescaleDB)
    time = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    video_id = Column(String, nullable=False)
    
    # --- Statistics (The Numbers) ---
    views = Column(BigInteger, nullable=False)
    likes = Column(BigInteger, nullable=False)
    comments = Column(BigInteger, nullable=False)
    
    # --- Metadata (The Text) ---
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    
    # --- UI Assets (The Visuals) ---
    thumbnail_url = Column(String, nullable=True)

    # Explicit Primary Key Constraint
    __table_args__ = (
        PrimaryKeyConstraint('video_id', 'time'),
    )

    @property
    def video_link(self):
        """
        Generates the YouTube link on the fly.
        Usage in frontend: video_object.video_link
        """
        return f"[https://www.youtube.com/watch?v=](https://www.youtube.com/watch?v=){self.video_id}"

def init_db():
    """
    Creates the table and converts it to a Hypertable if it doesn't exist.
    """
    try:
        # 1. Create the standard table schema
        Base.metadata.create_all(bind=engine)
        
        # 2. Enable TimescaleDB Hypertable magic
        with engine.connect() as conn:
            conn.execute(text(
                "SELECT create_hypertable('video_stats','time',if_not_exists => TRUE);"
            ))
            conn.commit()
            print("Database initialized and Hypertable ready.")
    except Exception as e:
        print(f"Database Init Error: {e}")