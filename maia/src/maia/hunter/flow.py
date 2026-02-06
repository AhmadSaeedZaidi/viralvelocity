"""Maia Hunter: YouTube video discovery agent."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from atlas.adapters.maia import MaiaDAO
from atlas.utils import HydraExecutor, KeyRing
from atlas.vault import vault
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

hunter_keys = KeyRing("hunting")
hunter_executor = HydraExecutor(hunter_keys, agent_name="hunter")


@task(name="fetch_batch")  # type: ignore[misc]
async def fetch_batch(batch_size: int = 10) -> Any:
    dao = MaiaDAO()
    logger = get_run_logger()

    batch = await dao.fetch_hunter_batch(batch_size)
    if not batch:
        logger.info("Hunter Queue is empty. Sleeping...")
        return []

    logger.info(f"Fetched {len(batch)} targets from queue.")
    return batch


@task(name="search_youtube")  # type: ignore[misc]
async def search_youtube(topic: Dict[str, Any]) -> Any:
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
        return await hunter_executor.execute_async(make_request)
    except SystemExit:
        raise
    except Exception as e:
        run_logger.error(f"Search failed for '{query}': {e}")
        return None


@task(name="ingest_results")
async def ingest_results(topic: Dict[str, Any], response: Dict[str, Any]) -> None:
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
    logger = get_run_logger()

    items = response.get("items", [])
    next_token = response.get("nextPageToken")

    # 1. Ingest Video Metadata
    snowball_tags: List[str] = []

    for item in items:
        # Save raw to vault (Cold Archive)
        vid_id = item.get("id", {}).get("videoId")
        if vid_id:
            try:
                vault.store_metadata(vid_id, item)
            except Exception as e:
                logger.warning(f"Failed to store metadata for {vid_id} to vault: {e}")

        # Sync to DB (Hot Index)
        try:
            await dao.ingest_video_metadata(item)
        except Exception as e:
            logger.error(f"Failed to ingest video {vid_id} to database: {e}")
            continue

        # 2. Snowball Effect: Extract tags for the search queue
        snippet = item.get("snippet", {})
        tags = snippet.get("tags", [])
        if tags and isinstance(tags, list):
            # Filter out empty tags and normalize
            valid_tags = [str(tag).strip() for tag in tags if tag and len(str(tag).strip()) > 0]
            snowball_tags.extend(valid_tags)

    # 3. Feed tags back into search queue (Snowball)
    if snowball_tags:
        try:
            added_count = await dao.add_to_search_queue(snowball_tags)
            logger.info(
                f"Snowball Effect: Added {added_count} unique tags to search queue "
                f"(from {len(snowball_tags)} total tags)"
            )
        except Exception as e:
            logger.error(f"Failed to snowball tags into search queue: {e}")

    # 4. Update Topic State
    try:
        await dao.update_search_state(
            topic["id"],
            next_token,
            len(items),
            status="active" if next_token else "exhausted",
        )
    except Exception as e:
        logger.error(f"Failed to update search state for topic {topic['id']}: {e}")

    logger.info(
        f"Ingested {len(items)} videos for '{topic['query_term']}' "
        f"(snowballed {len(snowball_tags)} tags)"
    )


@flow(name="run_hunter_cycle")
async def run_hunter_cycle(batch_size: int = 10) -> Dict[str, Any]:
    """
    Execute a complete Hunter cycle: fetch queries, search YouTube, ingest results.

    Args:
        batch_size: Number of queries to process in this cycle

    Returns:
        Dictionary with cycle statistics
    """
    logger = get_run_logger()
    logger.info("=== Starting Hunter Cycle ===")

    stats = {
        "queries_processed": 0,
        "videos_discovered": 0,
        "searches_successful": 0,
        "searches_failed": 0,
    }

    try:
        targets = await fetch_batch(batch_size)

        if not targets:
            logger.info("No queries in queue. Hunter cycle complete (idle).")
            return stats

        stats["queries_processed"] = len(targets)

        for topic in targets:
            try:
                result = await search_youtube(topic)
                if result:
                    await ingest_results(topic, result)
                    items = result.get("items", [])
                    stats["videos_discovered"] += len(items)
                    stats["searches_successful"] += 1
                else:
                    stats["searches_failed"] += 1
            except SystemExit:
                # Resiliency strategy: Don't catch - let it propagate
                raise
            except Exception as e:
                logger.error(f"Error processing topic '{topic.get('query_term')}': {e}")
                stats["searches_failed"] += 1

        logger.info(
            f"=== Hunter Cycle Complete === "
            f"Processed: {stats['queries_processed']}, "
            f"Discovered: {stats['videos_discovered']}, "
            f"Success: {stats['searches_successful']}, "
            f"Failed: {stats['searches_failed']}"
        )

    except SystemExit:
        # Resiliency strategy: Rate limit detected - propagate immediately
        logger.critical("Hunter Cycle terminated by Resiliency strategy (429 Rate Limit)")
        raise
    except Exception as e:
        logger.exception(f"Hunter cycle failed with unexpected error: {e}")
        raise

    return stats


def main() -> None:
    """Entry point for running the Hunter as a standalone service."""
    try:
        asyncio.run(run_hunter_cycle())  # type: ignore[arg-type]
    except SystemExit as e:
        # Resiliency strategy: Exit with specific code for rate limit
        logger.critical(f"Hunter terminated: {e}")
        raise
    except KeyboardInterrupt:
        logger.info("Hunter stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Hunter failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
