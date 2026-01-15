"""Maia Janitor: Hot queue cleanup agent."""

import asyncio
import logging
from typing import Dict, Any

from atlas.adapters.maia import MaiaDAO
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)


@task(name="run_janitor_cleanup")
async def run_janitor_cleanup(dry_run: bool = False) -> Dict[str, Any]:
    dao = MaiaDAO()
    run_logger = get_run_logger()
    
    run_logger.info(f"Starting Janitor cleanup (dry_run={dry_run})...")
    
    result = await dao.run_janitor(dry_run=dry_run)
    
    if dry_run:
        run_logger.info(
            f"Janitor [DRY RUN]: Would delete {result.get('would_delete', 0)} videos"
        )
    else:
        run_logger.info(
            f"Janitor: Cleaned up {result.get('deleted', 0)} videos "
            f"(retention: {result.get('retention_days', 'N/A')} days)"
        )
    
    return result


@flow(name="janitor_cycle")
async def janitor_cycle(dry_run: bool = False) -> None:
    """
    Prefect flow for running the Janitor cleanup cycle.
    
    This flow should be scheduled to run periodically (e.g., daily)
    to keep the hot queue size under control.
    
    Example scheduling:
        - Daily at 3 AM UTC
        - After major processing cycles complete
        - When database size alerts trigger
    """
    run_logger = get_run_logger()
    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE STARTING")
    run_logger.info("=" * 60)
    
    result = await run_janitor_cleanup(dry_run=dry_run)
    
    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE COMPLETE")
    run_logger.info(f"Result: {result}")
    run_logger.info("=" * 60)
    
    return result


def main() -> None:
    """Entry point for running the Janitor as a standalone service."""
    try:
        # Run with dry_run=True by default for safety
        asyncio.run(janitor_cycle(dry_run=True))
    except KeyboardInterrupt:
        logger.info("Janitor stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Janitor failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    main()
