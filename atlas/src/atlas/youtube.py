"""Thin async wrappers over the YouTube Data API v3 with key rotation.

Exposes ``lookup_videos`` and ``lookup_channels`` (≤50 IDs per call; callers
chunk ``ids``). Both use a KeyRing for rotation and back off via
ResiliencyExecutor on quota/429/403 errors.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import aiohttp

from atlas.utils import KeyRing, ResiliencyExecutor

logger = logging.getLogger("atlas.youtube")

_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_API_BATCH_LIMIT = 50


def _chunk(seq: Sequence[str], n: int) -> list[list[str]]:
    """Split ``seq`` into chunks of size ``n``."""
    return [list(seq[i : i + n]) for i in range(0, len(seq), n)]


async def _execute_get(
    url: str,
    params: dict[str, Any],
    executor: ResiliencyExecutor,
) -> dict[str, Any]:
    """Execute a single GET via the resiliency executor (key rotation on 429/403)."""

    async def make_request(api_key: str) -> dict[str, Any]:
        params_with_key = {**params, "key": api_key}
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, params=params_with_key, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp,
        ):
            text = await resp.text()
            if resp.status == 200:
                result: dict[str, Any] = json.loads(text) if text else {}
                return result
            if resp.status in (403, 429):
                raise Exception(f"HTTP {resp.status}: {text[:200]}")
            raise Exception(f"HTTP {resp.status}: {text[:200]}")

    result = await executor.execute_async(make_request)
    return result or {}


async def lookup_videos(
    video_ids: Sequence[str],
    *,
    executor: ResiliencyExecutor | None = None,
    key_ring_pool: str = "hunting",
    parts: str = "snippet,contentDetails,statistics",
) -> list[dict[str, Any]]:
    """Resolve full metadata for a list of YouTube video IDs (≤50 per call;
    chunked). Returns the API ``items`` list."""
    if not video_ids:
        return []

    if executor is None:
        executor = ResiliencyExecutor(KeyRing(key_ring_pool), agent_name="youtube_lookup")

    out: list[dict[str, Any]] = []
    for chunk in _chunk(list(video_ids), _API_BATCH_LIMIT):
        resp = await _execute_get(
            _VIDEOS_URL,
            {"part": parts, "id": ",".join(chunk), "maxResults": len(chunk)},
            executor,
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        out.extend(items)
        logger.info(f"lookup_videos: resolved {len(items)}/{len(chunk)} video records")
    return out


async def lookup_channels(
    channel_ids: Sequence[str],
    *,
    executor: ResiliencyExecutor | None = None,
    key_ring_pool: str = "hunting",
    parts: str = "snippet,statistics",
) -> list[dict[str, Any]]:
    """Resolve full metadata for a list of YouTube channel IDs (≤50 per call;
    chunked). Returns the API ``items`` list."""
    if not channel_ids:
        return []

    if executor is None:
        executor = ResiliencyExecutor(KeyRing(key_ring_pool), agent_name="youtube_lookup")

    out: list[dict[str, Any]] = []
    for chunk in _chunk(list(channel_ids), _API_BATCH_LIMIT):
        resp = await _execute_get(
            _CHANNELS_URL,
            {"part": parts, "id": ",".join(chunk), "maxResults": len(chunk)},
            executor,
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        out.extend(items)
        logger.info(f"lookup_channels: resolved {len(items)}/{len(chunk)} channel records")
    return out
