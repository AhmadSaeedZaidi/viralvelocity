import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from prefect import flow, get_run_logger, task

from atlas.adapters.maia import MaiaDAO
from atlas.utils import KeyRing

# Specific KeyRing for this high-volume historical search
archeo_keys = KeyRing("archeology")

# Major Categories (Gaming, Entertainment, Music, Tech, Education)
# These are standard YouTube API Category IDs (US region mostly)
TARGET_CATEGORIES = ["10", "20", "24", "28", "27"]


@task(name="hunt_history")
async def hunt_history(year: int, month: int) -> None:
    """
    Searches for top videos in target categories for a specific month in history.
    """
    logger = get_run_logger()
    dao = MaiaDAO()

    # Calculate time window
    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    start_str = start_date.isoformat().replace("+00:00", "Z")
    end_str = end_date.isoformat().replace("+00:00", "Z")

    logger.info(f"Archeologist digging in: {start_str} to {end_str}")

    base_url = "https://www.googleapis.com/youtube/v3/search"

    for category in TARGET_CATEGORIES:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "viewCount",  # Find the 'Gold'
            "publishedAfter": start_str,
            "publishedBefore": end_str,
            "videoCategoryId": category,
            "maxResults": 50,
        }

        # Key Rotation Logic
        max_retries = archeo_keys.size
        for _ in range(max_retries):
            key = archeo_keys.next_key()
            params["key"] = key

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            items = data.get("items", [])

                            # Ingest with High Priority (100)
                            for item in items:
                                await dao.ingest_video_metadata(
                                    item, priority_override=100
                                )

                            logger.info(
                                f"Recovered {len(items)} relics from {year}-{month} (Cat: {category})"
                            )
                            break  # Success, move to next category

                        elif resp.status == 403:
                            logger.warning(
                                f"Archeologist Key {key[-6:]} burned. Rotating."
                            )
                            continue
                        elif resp.status == 429:
                            logger.critical("Archeologist hit 429. Aborting to Hydra.")
                            raise SystemExit("429 Rate Limit - Archeologist")
                        else:
                            logger.error(f"HTTP {resp.status} for historical search")
                            break
            except Exception as e:
                if isinstance(e, SystemExit):
                    raise
                logger.error(f"Network error in Archeologist: {e}")


@flow(name="run_archeology_campaign")
async def run_archeology_campaign(start_year: int = 2005, end_year: int = 2024):
    """
    The Campaign. Iterates through history.
    WARNING: This consumes massive quota. Run sparingly.
    """
    logger = get_run_logger()
    logger.info("Starting Archeology Campaign...")

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            await hunt_history(year, month)
            # Sleep slightly to be polite to the API?
            # Nah, let Hydra handle the limits.


if __name__ == "__main__":
    # Example usage: Dig up 2010
    asyncio.run(run_archeology_campaign(start_year=2010, end_year=2010))
