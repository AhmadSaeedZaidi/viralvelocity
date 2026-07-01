"""Maia Archeologist: Historical video discovery agent.

Producer in the Producer-Consumer pipeline. Sole responsibility is to
discover historical video IDs from past years and push them to the
video table (the work queue for downstream consumers).
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from atlas.repositories import VideoRepository
from prefect import flow, get_run_logger, task

from maia.hunter.flow import enrich_channels_task
from maia.strategies import YouTubeSearchStrategy
from maia.utils import RateLimitError

logger = logging.getLogger(__name__)

TARGET_CATEGORIES = ["10", "20", "24", "28", "27"]


@task(name="hunt_history")
async def hunt_history_task(
    year: int, month: int, strategy: YouTubeSearchStrategy
) -> None:
    """Search for top videos in target categories for a specific month in history."""
    run_logger = get_run_logger()
    video_repo = VideoRepository()

    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    start_str = start_date.isoformat().replace("+00:00", "Z")
    end_str = end_date.isoformat().replace("+00:00", "Z")

    run_logger.info(f"Archeologist digging in: {start_str} to {end_str}")

    for category in TARGET_CATEGORIES:
        params: Dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "order": "viewCount",
            "publishedAfter": start_str,
            "publishedBefore": end_str,
            "videoCategoryId": category,
            "maxResults": 50,
        }

        try:
            data = await strategy.search(params)
            if not data:
                run_logger.warning(
                    f"No data returned for {year}-{month} (Cat: {category})"
                )
                continue

            items = data.get("items", [])

            ingest_tasks = [
                video_repo.ingest_video_metadata(item, priority_override=100)
                for item in items
            ]
            await asyncio.gather(*ingest_tasks)

            channel_ids = [
                it.get("snippet", {}).get("channelId")
                for it in items
                if it.get("snippet", {}).get("channelId")
            ]
            if channel_ids:
                try:
                    n = await enrich_channels_task(channel_ids, strategy)
                    run_logger.info(
                        f"Enriched {n} channel(s) for recovered relics (Cat: {category})"
                    )
                except RateLimitError:
                    raise
                except Exception as e:
                    run_logger.warning(
                        f"Channel enrichment after archeology ingest (non-fatal): {e}"
                    )

            run_logger.info(
                f"Recovered {len(items)} relics from {year}-{month} (Cat: {category})"
            )

        except RateLimitError:
            run_logger.critical(
                f"Archeologist rate-limited on {year}-{month} (Cat: {category})"
            )
            raise
        except Exception as e:
            run_logger.error(
                f"Archeologist error on {year}-{month} (Cat: {category}): {e}"
            )
            continue


@flow(name="run_archeology_campaign")
async def archeology_flow(
    start_year: int, end_year: int, strategy: YouTubeSearchStrategy
) -> Dict[str, Any]:
    """
    Execute an archeology campaign to discover historical videos.

    WARNING: This consumes massive quota. Run sparingly.

    Args:
        start_year: Start year for historical campaign
        end_year: End year for historical campaign
        strategy: YouTubeSearchStrategy for API access

    Returns:
        Dictionary with campaign statistics
    """
    run_logger = get_run_logger()
    run_logger.info("Starting Archeology Campaign...")

    stats = {
        "years_processed": 0,
        "months_processed": 0,
        "videos_discovered": 0,
    }

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            await hunt_history_task(year, month, strategy)
            stats["months_processed"] += 1
        stats["years_processed"] += 1

    run_logger.info(f"Archeology Campaign Complete: {stats}")
    return stats


class ArcheologistAgent:
    """
    Archeologist Agent: Historical video discovery from past years.

    Implements the Agent protocol for polymorphic command dispatch.
    """

    name = "archeologist"

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.name)
        self.strategy = YouTubeSearchStrategy("archeology", agent_name="archeologist")

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--start-year",
            type=int,
            default=2005,
            help="Start year for historical campaign (default: 2005)",
        )
        parser.add_argument(
            "--end-year",
            type=int,
            default=2024,
            help="End year for historical campaign (default: 2024)",
        )

    async def run(
        self, start_year: int = 2005, end_year: int = 2024, **kwargs: Any
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = await archeology_flow(
            start_year=start_year, end_year=end_year, strategy=self.strategy
        )
        return result


@flow(name="run_archeology_campaign")
async def run_archeology_campaign(start_year: int = 2005, end_year: int = 2024) -> None:
    agent = ArcheologistAgent()
    await agent.run(start_year=start_year, end_year=end_year)


@task(name="hunt_history")
async def hunt_history(year: int, month: int) -> None:
    agent = ArcheologistAgent()
    await hunt_history_task(year, month, agent.strategy)


if __name__ == "__main__":
    agent = ArcheologistAgent()
    asyncio.run(agent.run(start_year=2010, end_year=2010))
