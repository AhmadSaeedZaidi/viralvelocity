"""Strategy pattern for YouTube Data API scraping.

Encapsulates core HTTP request, key rotation, and rate-limit backoff logic
shared by the Hunter and Archeologist producers, plus the Tracker consumer.
"""

from abc import ABC
from typing import Any, Dict, Optional

import aiohttp

from atlas.utils import KeyRing, ResiliencyExecutor

from maia.utils import RateLimitError, execute_with_rate_limit


class YouTubeSearchStrategy(ABC):
    """Base class for YouTube Data API search and video resolution.

    Provides ``execute_get()`` which handles key rotation and Resiliency
    Strategy termination (``SystemExit`` → ``RateLimitError``) so that all
    callers get a uniform interface.

    Subclasses should call ``await self.execute_get(url, params)`` and
    implement their own response-processing logic.
    """

    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

    def __init__(self, key_ring_pool: str, agent_name: str) -> None:
        self.keys = KeyRing(key_ring_pool)
        self.executor = ResiliencyExecutor(self.keys, agent_name=agent_name)

    async def execute_get(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute an authenticated GET request with key rotation and rate-limit handling.

        Returns:
            Parsed JSON body on success, or ``None`` on non-retryable errors.

        Raises:
            RateLimitError: When all keys are exhausted (Resiliency Strategy).
        """

        async def make_request(api_key: str) -> Dict[str, Any]:
            params_with_key: Dict[str, Any] = {**params, "key": api_key}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params_with_key) as resp:
                    if resp.status == 200:
                        result: Dict[str, Any] = await resp.json()
                        return result
                    elif resp.status in (403, 429):
                        error_text = await resp.text()
                        raise Exception(f"HTTP {resp.status}: {error_text[:200]}")
                    else:
                        run_logger = __import__("logging").getLogger(  # noqa: E501
                            f"maia.strategies.{self.executor.agent_name}"
                        )
                        error_text = await resp.text()
                        run_logger.error(
                            f"HTTP {resp.status} for {url}: {error_text[:200]}"
                        )
                        raise Exception(f"HTTP {resp.status}: {error_text[:200]}")

        return await execute_with_rate_limit(self.executor, make_request)

    async def search(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Perform a ``youtube/v3/search`` request.

        Subclasses should populate *params* with the desired search criteria
        (query, category, publishedAfter, etc.) before calling this.
        """
        return await self.execute_get(self.SEARCH_URL, params)

    async def fetch_videos(
        self, video_ids: list[str], parts: str = "statistics"
    ) -> Optional[Dict[str, Any]]:
        """Resolve statistics/details for a batch of video IDs (max 50 per call)."""
        params: Dict[str, Any] = {"part": parts, "id": ",".join(video_ids)}
        return await self.execute_get(self.VIDEOS_URL, params)
