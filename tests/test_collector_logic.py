import datetime
import os
import sys

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from collector.models import VideoStat


def test_video_link_generation():
    """
    Verifies that the video_link property generates the correct URL.
    """
    mock_stat = VideoStat(
        time=datetime.datetime.now(),
        video_id="dQw4w9WgXcQ",
        views=100, likes=10, comments=5
    )
    expected_link = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert mock_stat.video_link == expected_link

def test_rich_metadata_storage():
    """
    Verifies the model can handle the new ML-focused fields.
    """
    mock_stat = VideoStat(
        time=datetime.datetime.now(),
        video_id="123",
        views=5000, likes=100, comments=50,
        title="Python Tutorial",
        description="A long description...",
        tags="python,coding,ai",
        # New Fields
        category_id="28",          # Tech
        duration_seconds=600,      # 10 minutes
        definition="hd",
        published_at=datetime.datetime(2023, 1, 1)
    )

    assert mock_stat.category_id == "28"
    assert mock_stat.duration_seconds == 600
    assert mock_stat.definition == "hd"
    assert "ai" in mock_stat.tags