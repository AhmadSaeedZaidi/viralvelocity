"""
Pytest configuration and fixtures for Maia tests.
"""

from typing import Any, Dict

import pytest


@pytest.fixture
def mock_youtube_search_response() -> Dict[str, Any]:
    """Mock YouTube Search API response."""
    return {
        "kind": "youtube#searchListResponse",
        "etag": "test-etag",
        "nextPageToken": "NEXT_PAGE_TOKEN",
        "items": [
            {
                "kind": "youtube#searchResult",
                "etag": "test-video-etag",
                "id": {"kind": "youtube#video", "videoId": "dQw4w9WgXcQ"},
                "snippet": {
                    "publishedAt": "2023-01-01T00:00:00Z",
                    "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
                    "title": "Test Video",
                    "channelTitle": "Test Channel",
                    "tags": ["test", "example", "ai"],
                    "categoryId": "28",
                    "defaultLanguage": "en",
                },
            }
        ],
    }


@pytest.fixture
def mock_youtube_stats_response() -> Dict[str, Any]:
    """Mock YouTube Videos API statistics response."""
    return {
        "kind": "youtube#videoListResponse",
        "etag": "test-etag",
        "items": [
            {
                "kind": "youtube#video",
                "etag": "test-etag",
                "id": "dQw4w9WgXcQ",
                "statistics": {
                    "viewCount": "1000000",
                    "likeCount": "50000",
                    "commentCount": "1000",
                },
            }
        ],
    }


@pytest.fixture
def mock_search_queue_item() -> Dict[str, Any]:
    """Mock search queue item from database."""
    return {
        "id": 1,
        "query_term": "artificial intelligence",
        "next_page_token": None,
        "last_searched_at": None,
        "priority": 5,
    }


@pytest.fixture
def mock_tracker_target() -> Dict[str, Any]:
    """Mock tracker target video from database."""
    return {
        "id": "dQw4w9WgXcQ",
        "title": "Test Video",
        "published_at": "2023-01-01T00:00:00Z",
        "last_updated_at": None,
    }
