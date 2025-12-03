import sys
import os
from unittest.mock import patch, MagicMock

# Fix path import
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from collector.yt_scraper import get_youtube_data

@patch("collector.yt_scraper.build")
def test_get_youtube_data_parsing(mock_build):
    """
    Tests that the scraper correctly parses nested JSON (duration, definition, tags)
    from the YouTube API response.
    """
    # 1. Setup the Mock
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    # 2. Create a Fake "Rich" Response
    fake_response = {
        "items": [
            {
                "id": "video_123",
                "statistics": {
                    "viewCount": "1000",
                    "likeCount": "100",
                    "commentCount": "50"
                },
                "snippet": {
                    "title": "My Viral Video",
                    "description": "Best video ever",
                    "tags": ["funny", "viral"],
                    "categoryId": "24",
                    "publishedAt": "2023-12-01T10:00:00Z",
                    "thumbnails": {
                        "high": {"url": "http://img.com/high.jpg"}
                    }
                },
                "contentDetails": {
                    "duration": "PT5M30S",  # 5 min 30 sec
                    "definition": "hd"
                }
            }
        ]
    }

    # Inject the fake response
    mock_service.videos.return_value.list.return_value.execute.return_value = fake_response

    # 3. Run the scraper logic
    # Use .fn to bypass the Prefect @task engine for Unit Testing.
    results = get_youtube_data.fn(["video_123"])

    # 4. Assertions
    assert len(results) == 1
    video = results[0]
    
    # Basic Stats
    assert video.views == 1000
    assert video.title == "My Viral Video"
    
    # New Rich Metadata Checks
    assert video.category_id == "24"
    assert video.definition == "hd"
    assert video.duration_seconds == 330  # (5 * 60) + 30
    assert "viral" in video.tags
    assert video.thumbnail_url == "http://img.com/high.jpg"