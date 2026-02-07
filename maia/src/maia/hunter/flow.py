"""Maia Hunter: YouTube video discovery agent."""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import aiohttp
from atlas.adapters.maia import MaiaDAO
from atlas.utils import HydraExecutor, KeyRing
from atlas.vault import vault
from prefect import flow, get_run_logger, task

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
    topic: Dict[str, Any], executor: HydraExecutor
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
        return await executor.execute_async(make_request)
    except SystemExit:
        raise
    except Exception as e:
        run_logger.error(f"Search failed for '{query}': {e}")
        return None


@task(name="ingest_results")
async def ingest_results_task(topic: Dict[str, Any], response: Dict[str, Any]) -> None:
    """
    Ingest video metadata and implement the Snowball Effect.

    Steps:
    1. Store raw metadata to vault (cold archive)
    2. Ingest structured metadata to database (hot index)
    3. Extract tags from all videos (Snowball)
    4. Add unique tags to search queue
    5. Update topic state with pagination token
    """
    if not response:
        return

    dao = MaiaDAO()
    run_logger = get_run_logger()

    items = response.get("items", [])
    next_token = response.get("nextPageToken")

    snowball_tags: List[str] = []

    for item in items:
        vid_id = item.get("id", {}).get("videoId")
        if vid_id:
            try:
                vault.store_metadata(vid_id, item)
            except Exception as e:
                run_logger.warning(f"Failed to store metadata for {vid_id} to vault: {e}")

        try:
            await dao.ingest_video_metadata(item)
        except Exception as e:
            run_logger.error(f"Failed to ingest video {vid_id} to database: {e}")
            continue

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
async def hunter_flow(batch_size: int, executor: HydraExecutor) -> Dict[str, Any]:
    """
    Execute a complete Hunter cycle: fetch queries, search YouTube, ingest results.

    Args:
        batch_size: Number of queries to process in this cycle
        executor: HydraExecutor for API key rotation

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
                    await ingest_results_task(topic, result)
                    items = result.get("items", [])
                    stats["videos_discovered"] += len(items)
                    stats["searches_successful"] += 1
                else:
                    stats["searches_failed"] += 1
            except SystemExit:
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

    except SystemExit:
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
        self.executor = HydraExecutor(self.keys, agent_name="hunter")

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
    executor = HydraExecutor(keys, agent_name="hunter")
    return await search_youtube_task(topic, executor)


@task(name="ingest_results")
async def ingest_results(topic: Dict[str, Any], response: Dict[str, Any]) -> None:
    """Legacy function wrapper for backward compatibility."""
    await ingest_results_task(topic, response)


def main() -> None:
    """Entry point for running the Hunter as a standalone service."""
    try:
        agent = HunterAgent()
        asyncio.run(agent.run())
    except SystemExit as e:
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
