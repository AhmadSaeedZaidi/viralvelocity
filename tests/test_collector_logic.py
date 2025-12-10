import datetime

import pytest

from collector.models import Video, VideoStat


def test_video_link_generation():
    """
    Verifies that the video_link property generates the correct URL.
    This property now lives on the 'Video' model.
    """
    mock_video = Video(
        video_id="dQw4w9WgXcQ",
        title="Rick Roll"
    )
    expected_link = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    
    # Check the property on the Video object
    assert mock_video.video_link == expected_link

def test_rich_metadata_storage():
    """
    Verifies the model separation handles fields correctly.
    Metadata goes to Video, Metrics go to VideoStat.
    """
    # 1. Metadata Object (Video)
    mock_video = Video(
        video_id="123",
        title="Python Tutorial",
        description="A long description...",
        tags="python,coding,ai",
        category_id="28",          # Tech
        duration_seconds=600,      # 10 minutes
        definition="hd",
        published_at=datetime.datetime(2023, 1, 1),
        made_for_kids=False
    )

    # 2. Stats Object (VideoStat)
    mock_stat = VideoStat(
        time=datetime.datetime.now(),
        video_id="123",
        views=5000,
        likes=100,
        comments=50
    )

    # Assertions
    assert mock_video.title == "Python Tutorial"
    assert mock_video.duration_seconds == 600
    assert mock_stat.views == 5000
    
    # Verify we can't accidentally put stats in metadata or vice versa
    with pytest.raises(TypeError):
        Video(video_id="456", views=1000) # Invalid: Views belong in Stat
        
    with pytest.raises(TypeError):
        VideoStat(video_id="456", title="Test") # Invalid: Title belongs in Video