"""Maia Hunter: YouTube video discovery agent.

Producer in the Producer-Consumer pipeline. Sole responsibility is to
identify target video IDs from the search queue and push them to the
video table (the work queue for Scribe, Painter, Tracker).
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas.notifications import AlertChannel, AlertLevel, notifier
from atlas.repositories import (
    ChannelRepository,
    SearchQueueRepository,
    VideoRepository,
    WatchlistRepository,
)
from atlas.state import clear_quota_exhausted
from atlas.utils import QuotaExhaustedError
from atlas.vault import get_vault
from atlas.youtube import lookup_channels
from prefect import flow, get_run_logger, task

from maia.quality import filter_by_quality
from maia.strategies import YouTubeSearchStrategy
from maia.utils import (
    channel_ids_of,
    cli_bootstrap,
    notify_quota_exhausted,
    run_agent_main,
    video_id_of,
)

logger = logging.getLogger(__name__)


@task(name="fetch_batch")
async def fetch_batch_task(batch_size: int) -> list[dict[str, Any]]:
    """Fetch a batch of queries from the search queue."""
    repo = SearchQueueRepository()
    run_logger = get_run_logger()

    batch = await repo.fetch_batch(batch_size)
    if not batch:
        run_logger.info("Hunter Queue is empty. Sleeping...")
        return []

    run_logger.info(f"Fetched {len(batch)} targets from queue.")
    return [
        {
            "id": item.id,
            "query_term": item.query_term,
            "next_page_token": item.next_page_token,
            "last_searched_at": item.last_searched_at,
            "priority": item.priority,
        }
        for item in batch
    ]


@task(name="search_youtube")
async def search_youtube_task(
    topic: dict[str, Any], strategy: YouTubeSearchStrategy
) -> dict[str, Any] | None:
    """Search YouTube API for videos matching the topic query."""
    run_logger = get_run_logger()
    query = topic["query_term"]
    page_token = topic.get("next_page_token")

    last_searched = topic.get("last_searched_at")
    if last_searched:
        if last_searched.tzinfo is None:
            last_searched = last_searched.replace(tzinfo=UTC)
        if datetime.now(UTC) - last_searched > timedelta(hours=12):
            run_logger.info(f"Topic '{query}' token is stale. Resetting.")
            page_token = None

    yesterday = datetime.now(UTC) - timedelta(hours=24)
    published_after = yesterday.isoformat()

    params: dict[str, Any] = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 50,
        "order": "date",
        "publishedAfter": published_after,
    }
    if page_token:
        params["pageToken"] = page_token

    try:
        return await strategy.search(params)
    except QuotaExhaustedError:
        await notify_quota_exhausted("hunter")
        raise
    except Exception as e:
        run_logger.exception(f"Search failed for '{query}': {e}")
        return None


@task(name="enrich_channels")
async def enrich_channels_task(channel_ids: list[str], strategy: YouTubeSearchStrategy) -> int:
    """Resolve unindexed/stale channels via the YouTube Data API + log a stats snapshot.

    Returns the number of channels actually refreshed (real API calls + DB writes).
    """
    if not channel_ids:
        return 0

    channel_repo = ChannelRepository()
    run_logger = get_run_logger()

    unique = list({cid for cid in channel_ids if cid})

    needs: list[str] = []
    for cid in unique:
        try:
            if await channel_repo.needs_refresh(cid):
                needs.append(cid)
        except Exception as e:
            run_logger.warning(f"channel_repo.needs_refresh({cid}) failed: {e}")

    if not needs:
        run_logger.info("No channels need enrichment (all have recent stats).")
        return 0

    run_logger.info(f"Enriching {len(needs)} channel(s) via YouTube channels.list API")

    try:
        items = await lookup_channels(needs, executor=strategy.executor)
    except QuotaExhaustedError:
        await notify_quota_exhausted("hunter")
        raise
    except Exception as e:
        run_logger.exception(f"channels.list lookup failed for {len(needs)} ids: {e}")
        return 0

    written = 0
    for item in items:
        try:
            await channel_repo.ingest_channel_snapshot(item)
            written += 1
        except Exception as e:
            run_logger.exception(f"Failed to ingest channel snapshot for {item.get('id')}: {e}")

    run_logger.info(f"Enriched {written}/{len(needs)} channel records (API hit, snapshot logged).")
    return written


@task(name="ingest_results")
async def ingest_results_task(
    topic: dict[str, Any],
    response: dict[str, Any],
    strategy: YouTubeSearchStrategy | None = None,
) -> None:
    """Store raw metadata to the vault, ingest structured metadata to the DB,
    enrich newly-discovered channels, snowball tags, and update topic state.
    """
    if not response:
        return

    video_repo = VideoRepository()
    search_repo = SearchQueueRepository()
    run_logger = get_run_logger()

    raw_items = response.get("items", [])
    next_token = response.get("nextPageToken")

    # 0. QUALITY GATE — reject Shorts / low-traction / low-engagement videos
    #    before anything is persisted or snowballed.
    executor = strategy.executor if strategy is not None else None
    try:
        items = await filter_by_quality(raw_items, executor, logger=run_logger)
    except QuotaExhaustedError:
        await notify_quota_exhausted("hunter")
        raise
    except Exception as e:
        # Enrichment failure is non-fatal: fall back to unfiltered ingest so we
        # don't lose discovery, but warn loudly.
        run_logger.warning(f"Quality gate enrichment failed, ingesting unfiltered: {e}")
        items = raw_items

    # Bundle ALL discovered videos into ONE vault commit to stay under
    # HuggingFace's 128-commits/hour account cap.
    v = get_vault()

    date_key = datetime.now(UTC).strftime("%Y-%m-%d")
    vault_items: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        vid_id = video_id_of(item)
        if vid_id:
            vault_items.append((f"metadata/{date_key}/{vid_id}.json", item))

    if vault_items:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, v.store_batch, vault_items)
        except Exception as e:
            run_logger.warning(
                f"Failed to batch-store metadata for {len(vault_items)} videos to vault: {e}"
            )

    # 2. Ingest structured metadata to database concurrently
    async def _db_ingest(item: dict[str, Any]) -> None:
        vid_id = video_id_of(item)
        try:
            await video_repo.ingest_video_metadata(item)
            if vid_id:
                # Adaptive Scheduling: every discovered video joins the persistent
                # watchlist so tracking survives janitor cleanup of the videos row.
                await WatchlistRepository().add(vid_id, tier="HOURLY")
        except Exception as e:
            run_logger.exception(f"Failed to ingest video {vid_id} to database: {e}")

    db_tasks = [_db_ingest(item) for item in items]

    await asyncio.gather(*db_tasks)

    if strategy is not None:
        channel_ids = channel_ids_of(items)
        try:
            await enrich_channels_task(channel_ids, strategy)
        except QuotaExhaustedError:
            await notify_quota_exhausted("hunter")
            raise
        except Exception as e:
            run_logger.exception(f"Channel enrichment failed (non-fatal): {e}")

    # Snowball tags are taken only from videos that passed the quality gate.
    snowball_tags: list[str] = []
    for item in items:
        snippet = item.get("snippet", {})
        tags = snippet.get("tags", [])
        if tags and isinstance(tags, list):
            valid_tags = [str(tag).strip() for tag in tags if tag and len(str(tag).strip()) > 0]
            snowball_tags.extend(valid_tags)

    if snowball_tags:
        try:
            added_count = await search_repo.add_terms(snowball_tags)
            run_logger.info(
                f"Snowball Effect: Added {added_count} unique tags to search queue "
                f"(from {len(snowball_tags)} total tags)"
            )
        except Exception as e:
            run_logger.exception(f"Failed to snowball tags into search queue: {e}")

    try:
        await search_repo.update_state(
            topic["id"],
            next_token,
            len(items),
            status="active" if next_token else "exhausted",
        )
    except Exception as e:
        run_logger.exception(f"Failed to update search state for topic {topic['id']}: {e}")

    run_logger.info(
        f"Ingested {len(items)}/{len(raw_items)} videos for '{topic['query_term']}' "
        f"(passed quality gate; snowballed {len(snowball_tags)} tags)"
    )


@flow(name="run_hunter_cycle")
async def hunter_flow(batch_size: int, strategy: YouTubeSearchStrategy) -> dict[str, Any]:
    """Execute a complete Hunter cycle: fetch queries, search YouTube, ingest results.

    Args:
        batch_size: Number of queries to process in this cycle.
        strategy: YouTubeSearchStrategy for API access.

    Returns a dict with cycle statistics.
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
            clear_quota_exhausted("hunter")
            return stats

        stats["queries_processed"] = len(targets)

        for topic in targets:
            try:
                result = await search_youtube_task(topic, strategy)
                if result:
                    await ingest_results_task(topic, result, strategy)
                    items = result.get("items", [])
                    stats["videos_discovered"] += len(items)
                    stats["searches_successful"] += 1
                else:
                    stats["searches_failed"] += 1
            except QuotaExhaustedError:
                await notify_quota_exhausted("hunter")
                raise
            except Exception as e:
                run_logger.exception(f"Error processing topic '{topic.get('query_term')}': {e}")
                stats["searches_failed"] += 1

        run_logger.info(
            f"=== Hunter Cycle Complete === "
            f"Processed: {stats['queries_processed']}, "
            f"Discovered: {stats['videos_discovered']}, "
            f"Success: {stats['searches_successful']}, "
            f"Failed: {stats['searches_failed']}"
        )

        await notifier.send(
            title="Hunter Cycle Summary",
            description=(
                f"Queries: {stats['queries_processed']} | "
                f"Discovered: {stats['videos_discovered']} videos | "
                f"Success: {stats['searches_successful']} | "
                f"Failed: {stats['searches_failed']}"
            ),
            channel=AlertChannel.HUNT,
            level=AlertLevel.INFO if stats['searches_failed'] == 0 else AlertLevel.WARNING,
            fields={
                "Queries Processed": str(stats["queries_processed"]),
                "Videos Discovered": str(stats["videos_discovered"]),
                "Searches Successful": str(stats["searches_successful"]),
                "Searches Failed": str(stats["searches_failed"]),
            },
        )

    except QuotaExhaustedError:
        run_logger.critical("Hunter Cycle terminated — all API keys exhausted")
        raise
    except Exception as e:
        run_logger.exception(f"Hunter cycle failed with unexpected error: {e}")
        raise

    clear_quota_exhausted("hunter")
    return stats


class HunterAgent:
    """Hunter Agent: YouTube video discovery and ingestion."""

    name = "hunter"

    def __init__(self) -> None:
        """Initialize the Hunter agent with its YouTube search strategy."""
        self.logger = logging.getLogger(self.name)
        self.strategy = YouTubeSearchStrategy("hunting", agent_name="hunter")

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Hunter agent."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of queries to process per cycle (default: 10)",
        )

    async def run(self, batch_size: int = 10, **kwargs: Any) -> dict[str, Any]:
        """Execute a complete Hunter cycle and return its statistics dict."""
        result: dict[str, Any] = await hunter_flow(batch_size=batch_size, strategy=self.strategy)
        return result


@task(name="ingest_results")
async def ingest_results(topic: dict[str, Any], response: dict[str, Any]) -> None:
    """Legacy Task wrapper — delegates to :func:`ingest_results_task`."""
    await ingest_results_task(topic, response)


@flow(name="run_hunter_cycle")
async def run_hunter_cycle(batch_size: int = 10) -> dict[str, Any]:
    """Legacy function wrapper for backward compatibility."""
    agent = HunterAgent()
    return await agent.run(batch_size=batch_size)


def main() -> None:
    run_agent_main(HunterAgent().run, "hunter")


if __name__ == "__main__":
    cli_bootstrap()
    main()
