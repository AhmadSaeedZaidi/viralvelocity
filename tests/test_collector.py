import datetime

from collector.models import VideoStat


def test_video_link_generation():
    """
    Verifies that the video_link property generates the correct URL
    based on the video_id.
    """
    # 1. Create a dummy object (no DB needed!)
    mock_stat = VideoStat(
        time=datetime.datetime.now(),
        video_id="dQw4w9WgXcQ",
        views=100,
        likes=10,
        comments=5
    )

    # 2. Check the logic
    expected_link = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert mock_stat.video_link == expected_link

def test_video_stat_creation():
    """
    Verifies we can instantiate the model with metadata.
    """
    mock_stat = VideoStat(
        time=datetime.datetime.now(),
        video_id="123",
        views=0, likes=0, comments=0,
        title="Test Video",
        tags="tag1,tag2"
    )
    assert mock_stat.title == "Test Video"
    assert "tag1" in mock_stat.tags