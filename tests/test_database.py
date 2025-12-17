import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from collector.models import Base, SearchDiscovery, TrendingDiscovery, Video, VideoStat

# --- Fixtures ---


@pytest.fixture(scope="module")
def engine():
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"
    )
    return create_engine(db_url)


@pytest.fixture(scope="module")
def tables(engine):
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(engine, tables):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


# --- Tests ---


def test_video_creation(session):
    """Test creating a Video (Metadata) record."""
    new_video = Video(
        video_id="vid123",
        title="Test Video",
        description="A test description",
        published_at=datetime.now(timezone.utc),
        duration_seconds=120,
        made_for_kids=False,
    )
    session.add(new_video)
    session.commit()

    stored_video = session.query(Video).filter_by(video_id="vid123").first()
    assert stored_video is not None
    assert stored_video.title == "Test Video"
    assert stored_video.made_for_kids is False


def test_video_stats_relationship(session):
    """Test linking Time-Series stats to a Video."""
    # 1. Create Video
    vid = Video(video_id="stat_test_vid", title="Stats Test")
    session.add(vid)
    session.commit()

    # 2. Add Stats
    stat_entry = VideoStat(
        video_id="stat_test_vid",
        views=100,
        likes=10,
        comments=5,
        time=datetime.now(timezone.utc),
    )
    session.add(stat_entry)
    session.commit()

    # 3. Retrieve
    stats = session.query(VideoStat).filter_by(video_id="stat_test_vid").all()
    assert len(stats) == 1
    assert stats[0].views == 100


def test_discovery_logs(session):
    """Test that discovery logs can reference videos."""
    vid = Video(video_id="disc_vid", title="Discovery Test")
    session.add(vid)
    session.commit()

    search_log = SearchDiscovery(video_id="disc_vid", query="Minecraft")
    trend_log = TrendingDiscovery(video_id="disc_vid", rank=1)

    session.add_all([search_log, trend_log])
    session.commit()

    assert session.query(SearchDiscovery).count() == 1
    assert session.query(TrendingDiscovery).count() == 1
    assert session.query(SearchDiscovery).first().query == "Minecraft"
