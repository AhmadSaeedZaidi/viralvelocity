"""Maia Hunter: YouTube video discovery agent."""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from atlas.adapters.maia import MaiaDAO
from atlas.utils import KeyRing, ResiliencyExecutor
from atlas.vault import get_vault
from atlas.youtube import lookup_channels
from prefect import flow, get_run_logger, task

from maia.utils import RateLimitError, execute_with_rate_limit

logger = logging.getLogger(__name__)


@task(name="fetch_batch")
async def fetch_batch_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch a batch of queries from the search queue."""
    dao = MaiaDAO()
    run_logger = get_run_logger()

    batch = await dao.fetch_hunter_batch(batch_size)
    if not batch:
        run_logger.info("Hunter Queue is empty. Sleeping...")
        return []

    run_logger.info(f"Fetched {len(batch)} targets from queue.")
    return batch


@task(name="search_youtube")
async def search_youtube_task(
    topic: Dict[str, Any], executor: ResiliencyExecutor
) -> Dict[str, Any] | None:
    """Search YouTube API for videos matching the topic query."""
    run_logger = get_run_logger()
    query = topic["query_term"]
    page_token = topic.get("next_page_token")

    last_searched = topic.get("last_searched_at")
    if last_searched:
        if last_searched.tzinfo is None:
            last_searched = last_searched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last_searched > timedelta(hours=12):
            run_logger.info(f"Topic '{query}' token is stale. Resetting.")
            page_token = None

    yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
    published_after = yesterday.isoformat()

    base_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 50,
        "order": "date",
        "publishedAfter": published_after,
    }
    if page_token:
        params["pageToken"] = page_token

    async def make_request(api_key: str) -> Dict[str, Any]:
        params["key"] = api_key

        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params=params) as resp:
                if resp.status == 200:
                    result: Dict[str, Any] = await resp.json()
                    return result
                elif resp.status in (403, 429):
                    error_text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {error_text[:200]}")
                else:
                    run_logger.error(f"HTTP {resp.status} for {query}")
                    raise Exception(f"HTTP {resp.status}")

    try:
        return await execute_with_rate_limit(executor, make_request)
    except RateLimitError:
        raise
    except Exception as e:
        run_logger.error(f"Search failed for '{query}': {e}")
        return None


@task(name="enrich_channels")
async def enrich_channels_task(channel_ids: List[str], executor: ResiliencyExecutor) -> int:
    """Resolve unindexed/stale channels via the YouTube Data API + log a stats snapshot.

    For every channel id passed in:
    * If the channel has no row in ``channel_stats_log`` newer than 24h, it is
      fetched via ``channels.list?part=snippet,statistics`` and persisted via
      :meth:`MaiaDAO.ingest_channel_snapshot`.
    * Otherwise it is skipped.

    Returns the number of channels actually refreshed (real API calls + DB writes).
    """
    if not channel_ids:
        return 0

    dao = MaiaDAO()
    run_logger = get_run_logger()

    unique = list({cid for cid in channel_ids if cid})

    needs: List[str] = []
    for cid in unique:
        try:
            if await dao.channel_needs_refresh(cid):
                needs.append(cid)
        except Exception as e:
            run_logger.warning(f"channel_needs_refresh({cid}) failed: {e}")

    if not needs:
        run_logger.info("No channels need enrichment (all have recent stats).")
        return 0

    run_logger.info(f"Enriching {len(needs)} channel(s) via YouTube channels.list API")

    try:
        items = await lookup_channels(needs, executor=executor)
    except RateLimitError:
        raise
    except Exception as e:
        run_logger.error(f"channels.list lookup failed for {len(needs)} ids: {e}")
        return 0

    written = 0
    for item in items:
        try:
            await dao.ingest_channel_snapshot(item)
            written += 1
        except Exception as e:
            run_logger.error(f"Failed to ingest channel snapshot for {item.get('id')}: {e}")

    run_logger.info(f"Enriched {written}/{len(needs)} channel records (API hit, snapshot logged).")
    return written


@task(name="ingest_results")
async def ingest_results_task(
    topic: Dict[str, Any],
    response: Dict[str, Any],
    executor: Optional[ResiliencyExecutor] = None,
) -> None:
    """
    Ingest video metadata and implement the Snowball Effect.

    Steps:
    1. Store raw metadata to vault (cold archive) — concurrent, best-effort
    2. Ingest structured metadata to database (hot index) — concurrent
    3. Enrich newly-discovered channels via channels.list API + snapshot log
    4. Extract tags from all videos (Snowball)
    5. Add unique tags to search queue
    6. Update topic state with pagination token
    """
    if not response:
        return

    dao = MaiaDAO()
    run_logger = get_run_logger()

    items = response.get("items", [])
    next_token = response.get("nextPageToken")

    # 1. Store raw metadata to vault concurrently (best-effort, off event-loop)
    v = get_vault()

    async def _vault_store(vid_id: str, data: Dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: v.store_metadata(vid_id, data))
        except Exception as e:
            run_logger.warning(f"Failed to store metadata for {vid_id} to vault: {e}")

    vault_tasks = []
    for item in items:
        vid_id = item.get("id", {}).get("videoId")
        if vid_id:
            vault_tasks.append(_vault_store(vid_id, item))

    # 2. Ingest structured metadata to database concurrently
    async def _db_ingest(item: Dict[str, Any]) -> None:
        vid_id = item.get("id", {}).get("videoId") or item.get("id")
        try:
            await dao.ingest_video_metadata(item)
        except Exception as e:
            run_logger.error(f"Failed to ingest video {vid_id} to database: {e}")

    db_tasks = [_db_ingest(item) for item in items]

    # Fire vault stores and DB inserts concurrently
    await asyncio.gather(*vault_tasks, *db_tasks)

    # 3. Enrich newly-discovered channels (snippet -> channels.list -> stats log)
    if executor is not None:
        channel_ids = [
            item.get("snippet", {}).get("channelId")
            for item in items
            if item.get("snippet", {}).get("channelId")
        ]
        try:
            await enrich_channels_task(channel_ids, executor)
        except RateLimitError:
            raise
        except Exception as e:
            run_logger.error(f"Channel enrichment failed (non-fatal): {e}")

    # 4. Extract snowball tags (pure computation — no I/O)
    snowball_tags: List[str] = []
    for item in items:
        snippet = item.get("snippet", {})
        tags = snippet.get("tags", [])
        if tags and isinstance(tags, list):
            valid_tags = [str(tag).strip() for tag in tags if tag and len(str(tag).strip()) > 0]
            snowball_tags.extend(valid_tags)

    if snowball_tags:
        try:
            added_count = await dao.add_to_search_queue(snowball_tags)
            run_logger.info(
                f"Snowball Effect: Added {added_count} unique tags to search queue "
                f"(from {len(snowball_tags)} total tags)"
            )
        except Exception as e:
            run_logger.error(f"Failed to snowball tags into search queue: {e}")

    try:
        await dao.update_search_state(
            topic["id"],
            next_token,
            len(items),
            status="active" if next_token else "exhausted",
        )
    except Exception as e:
        run_logger.error(f"Failed to update search state for topic {topic['id']}: {e}")

    run_logger.info(
        f"Ingested {len(items)} videos for '{topic['query_term']}' "
        f"(snowballed {len(snowball_tags)} tags)"
    )


@flow(name="run_hunter_cycle")
async def hunter_flow(batch_size: int, executor: ResiliencyExecutor) -> Dict[str, Any]:
    """
    Execute a complete Hunter cycle: fetch queries, search YouTube, ingest results.

    Args:
        batch_size: Number of queries to process in this cycle
        executor: ResiliencyExecutor for API key rotation

    Returns:
        Dictionary with cycle statistics
    """
    run_logger = get_run_logger()
    run_logger.info("=== Starting Hunter Cycle ===")

    stats = {
        "queries_processed": 0,
        "videos_discovered": 0,
        "searches_successful": 0,
        "searches_failed": 0,
    }

    try:
        targets = await fetch_batch_task(batch_size)

        if not targets:
            run_logger.info("No queries in queue. Hunter cycle complete (idle).")
            return stats

        stats["queries_processed"] = len(targets)

        for topic in targets:
            try:
                result = await search_youtube_task(topic, executor)
                if result:
                    await ingest_results_task(topic, result, executor)
                    items = result.get("items", [])
                    stats["videos_discovered"] += len(items)
                    stats["searches_successful"] += 1
                else:
                    stats["searches_failed"] += 1
            except RateLimitError:
                raise
            except Exception as e:
                run_logger.error(f"Error processing topic '{topic.get('query_term')}': {e}")
                stats["searches_failed"] += 1

        run_logger.info(
            f"=== Hunter Cycle Complete === "
            f"Processed: {stats['queries_processed']}, "
            f"Discovered: {stats['videos_discovered']}, "
            f"Success: {stats['searches_successful']}, "
            f"Failed: {stats['searches_failed']}"
        )

    except RateLimitError:
        run_logger.critical("Hunter Cycle terminated by Resiliency strategy (429 Rate Limit)")
        raise
    except Exception as e:
        run_logger.exception(f"Hunter cycle failed with unexpected error: {e}")
        raise

    return stats


class HunterAgent:
    """
    Hunter Agent: YouTube video discovery and ingestion.

    Implements the Agent protocol for polymorphic command dispatch.
    """

    name = "hunter"

    def __init__(self) -> None:
        """Initialize the Hunter agent with its KeyRing and executor."""
        self.logger = logging.getLogger(self.name)
        self.keys = KeyRing("hunting")
        self.executor = ResiliencyExecutor(self.keys, agent_name="hunter")

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Hunter agent."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of queries to process per cycle (default: 10)",
        )

    async def run(self, batch_size: int = 10, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute a complete Hunter cycle.

        Args:
            batch_size: Number of queries to process in this cycle
            **kwargs: Additional arguments (ignored)

        Returns:
            Dictionary with cycle statistics
        """
        return await hunter_flow(batch_size=batch_size, executor=self.executor)


@flow(name="run_hunter_cycle")
async def run_hunter_cycle(batch_size: int = 10) -> Dict[str, Any]:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using HunterAgent directly for new code.
    """
    agent = HunterAgent()
    return await agent.run(batch_size=batch_size)


@task(name="fetch_batch")
async def fetch_batch(batch_size: int = 10) -> Any:
    """Legacy function wrapper for backward compatibility."""
    return await fetch_batch_task(batch_size)


@task(name="search_youtube")
async def search_youtube(topic: Dict[str, Any]) -> Any:
    """Legacy function wrapper for backward compatibility."""
    keys = KeyRing("hunting")
    executor = ResiliencyExecutor(keys, agent_name="hunter")
    return await search_youtube_task(topic, executor)


@task(name="ingest_results")
async def ingest_results(topic: Dict[str, Any], response: Dict[str, Any]) -> None:
    """Legacy function wrapper for backward compatibility."""
    await ingest_results_task(topic, response)


@task(name="enrich_channels")
async def enrich_channels(channel_ids: List[str]) -> int:
    """Legacy function wrapper for backward compatibility."""
    keys = KeyRing("hunting")
    executor = ResiliencyExecutor(keys, agent_name="hunter")
    return await enrich_channels_task(channel_ids, executor)


def main() -> None:
    """Entry point for running the Hunter as a standalone service."""
    try:
        agent = HunterAgent()
        asyncio.run(agent.run())
    except RateLimitError as e:
        logger.critical(f"Hunter terminated: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("Hunter stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Hunter failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
