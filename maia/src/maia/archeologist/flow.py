"""Maia Archeologist: Historical video discovery agent."""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Union

import aiohttp
from atlas.adapters.maia import MaiaDAO
from atlas.utils import KeyRing
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

TARGET_CATEGORIES = ["10", "20", "24", "28", "27"]


@task(name="hunt_history")
async def hunt_history_task(year: int, month: int, keys: KeyRing) -> None:
    """Search for top videos in target categories for a specific month in history."""
    run_logger = get_run_logger()
    dao = MaiaDAO()

    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    start_str = start_date.isoformat().replace("+00:00", "Z")
    end_str = end_date.isoformat().replace("+00:00", "Z")

    run_logger.info(f"Archeologist digging in: {start_str} to {end_str}")

    base_url = "https://www.googleapis.com/youtube/v3/search"

    for category in TARGET_CATEGORIES:
        params: Dict[str, Union[str, int]] = {
            "part": "snippet",
            "type": "video",
            "order": "viewCount",
            "publishedAfter": start_str,
            "publishedBefore": end_str,
            "videoCategoryId": category,
            "maxResults": 50,
        }

        max_retries = keys.size
        for _ in range(max_retries):
            key = keys.next_key()
            params["key"] = key

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            items = data.get("items", [])

                            for item in items:
                                await dao.ingest_video_metadata(item, priority_override=100)

                            run_logger.info(
                                f"Recovered {len(items)} relics from {year}-{month} (Cat: {category})"
                            )
                            break

                        elif resp.status == 403:
                            run_logger.warning(f"Archeologist Key {key[-6:]} burned. Rotating.")
                            continue
                        elif resp.status == 429:
                            run_logger.critical("Archeologist hit 429. Aborting to Resiliency.")
                            raise SystemExit("429 Rate Limit - Archeologist")
                        else:
                            run_logger.error(f"HTTP {resp.status} for historical search")
                            break
            except Exception as e:
                if isinstance(e, SystemExit):
                    raise
                run_logger.error(f"Network error in Archeologist: {e}")


@flow(name="run_archeology_campaign")
async def archeology_flow(start_year: int, end_year: int, keys: KeyRing) -> Dict[str, Any]:
    """
    Execute an archeology campaign to discover historical videos.

    WARNING: This consumes massive quota. Run sparingly.

    Args:
        start_year: Start year for historical campaign
        end_year: End year for historical campaign
        keys: KeyRing for API key rotation

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
            await hunt_history_task(year, month, keys)
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
        """Initialize the Archeologist agent with its KeyRing."""
        self.logger = logging.getLogger(self.name)
        self.keys = KeyRing("archeology")

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Archeologist agent."""
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
        """
        Execute an archeology campaign to discover historical videos.

        Args:
            start_year: Start year for historical campaign
            end_year: End year for historical campaign
            **kwargs: Additional arguments (ignored)

        Returns:
            Dictionary with campaign statistics
        """
        return await archeology_flow(start_year=start_year, end_year=end_year, keys=self.keys)


@flow(name="run_archeology_campaign")
async def run_archeology_campaign(start_year: int = 2005, end_year: int = 2024) -> None:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using ArcheologistAgent directly for new code.
    """
    agent = ArcheologistAgent()
    await agent.run(start_year=start_year, end_year=end_year)


@task(name="hunt_history")
async def hunt_history(year: int, month: int) -> None:
    """Legacy function wrapper for backward compatibility."""
    keys = KeyRing("archeology")
    await hunt_history_task(year, month, keys)


if __name__ == "__main__":
    agent = ArcheologistAgent()
    asyncio.run(agent.run(start_year=2010, end_year=2010))
