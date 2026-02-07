"""
Global pytest configuration for Alkyone.
Imports fixtures from src/alkyone/fixtures.py to make them available to all tests.
"""

from unittest.mock import AsyncMock, patch

import pytest

from alkyone.fixtures import (
    fresh_db,
    mock_search_queue_item,
    mock_tracker_target,
    mock_youtube_search_response,
    mock_youtube_stats_response,
    system_init,
)


@pytest.fixture
def mock_sleep():
    """Mock asyncio.sleep to speed up tests."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock
