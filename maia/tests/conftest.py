import logging
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_prefect_logger():
    """
    Globally mock get_run_logger to prevent MissingContextError in unit tests.
    We must patch it in every module that imports it directly.
    """
    # 1. The list of modules that use 'from prefect import get_run_logger'
    targets = [
        "prefect.get_run_logger",  # The source (just in case)
        "maia.hunter.flow.get_run_logger",
        "maia.tracker.flow.get_run_logger",
        "maia.janitor.flow.get_run_logger",
        "maia.archeologist.flow.get_run_logger",
        "maia.scribe.flow.get_run_logger",
        "maia.painter.flow.get_run_logger",
    ]

    # 2. Create a standard Python logger to return
    dummy_logger = logging.getLogger("test_logger")
    dummy_logger.setLevel(logging.INFO)

    # 3. Apply patches dynamically
    # We use a try/except block because some modules might not be imported yet,
    # or might not use the logger.
    patches = []
    for target in targets:
        try:
            p = patch(target, return_value=dummy_logger)
            p.start()
            patches.append(p)
        except (ImportError, AttributeError):
            # If the module isn't loaded or doesn't have the attribute, skip it
            pass

    yield dummy_logger

    # 4. Cleanup
    for p in patches:
        p.stop()


@pytest.fixture
def mock_sleep():
    """Skip sleeps in tests to make them fast."""
    with patch("asyncio.sleep", new_callable=MagicMock) as mock:

        async def instant_sleep(*args, **kwargs):
            return None

        mock.side_effect = instant_sleep
        yield mock


@pytest.fixture
def sample_video():
    """Standard video dict for testing."""
    return {"id": "VIDEO_001", "title": "Test Video", "channel_id": "CHANNEL_001"}
