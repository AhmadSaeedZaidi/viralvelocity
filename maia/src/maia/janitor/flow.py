"""Maia Janitor: Hot queue cleanup agent."""

import asyncio
import logging
from typing import Any, Dict

from prefect import flow, get_run_logger, task

from atlas.adapters.maia import MaiaDAO

logger = logging.getLogger(__name__)


@task(name="archive_cold_stats")  # type: ignore[misc]
async def archive_cold_stats_task(retention_days: int = 7) -> Any:
    """Archive stats older than retention_days from hot tier to cold tier (Vault)."""
    dao = MaiaDAO()
    run_logger = get_run_logger()

    run_logger.info(f"Starting stats archival (retention: {retention_days} days)...")

    total_archived = 0
    batch_count = 0

    # Loop until backlog is drained
    while True:
        try:
            archived = await dao.archive_cold_stats(retention_days=retention_days, batch_size=5000)
            if archived == 0:
                break

            total_archived += archived
            batch_count += 1
            run_logger.info(
                f"Batch {batch_count}: Archived {archived} stats (total: {total_archived})"
            )

            # Prevent tight loop
            await asyncio.sleep(1)

        except Exception as e:
            run_logger.error(f"Stats archival failed: {e}")
            raise

    run_logger.info(
        f"Stats archival complete: {total_archived} total rows archived in {batch_count} batches"
    )
    return {"archived": total_archived, "batches": batch_count}


@task(name="run_janitor_cleanup")  # type: ignore[misc]
async def run_janitor_cleanup(dry_run: bool = False) -> Any:
    dao = MaiaDAO()
    run_logger = get_run_logger()

    run_logger.info(f"Starting Janitor cleanup (dry_run={dry_run})...")

    result = await dao.run_janitor(dry_run=dry_run)

    if dry_run:
        run_logger.info(f"Janitor [DRY RUN]: Would delete {result.get('would_delete', 0)} videos")
    else:
        run_logger.info(
            f"Janitor: Cleaned up {result.get('deleted', 0)} videos "
            f"(retention: {result.get('retention_days', 'N/A')} days)"
        )

    return result


@flow(name="janitor_cycle")
async def janitor_cycle(dry_run: bool = False, archive_stats: bool = True) -> None:
    """
    Prefect flow for running the Janitor cleanup cycle.

    This flow:
    1. Archives old stats from hot tier (SQL) to cold tier (Vault)
    2. Cleans up old processed videos from hot queue

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

    results = {}

    # Step 1: Archive stats to cold tier
    if archive_stats and not dry_run:
        run_logger.info("Phase 1: Archiving stats to cold tier...")
        try:
            stats_result = await archive_cold_stats_task(retention_days=7)
            results["stats_archived"] = stats_result["archived"]
            results["archive_batches"] = stats_result["batches"]
        except Exception as e:
            run_logger.error(f"Stats archival failed: {e}")
            results["stats_archived"] = 0
            results["archive_error"] = str(e)
    else:
        run_logger.info("Phase 1: Stats archival skipped (dry_run or disabled)")
        results["stats_archived"] = 0

    # Step 2: Clean up old videos
    run_logger.info("Phase 2: Cleaning up old videos...")
    video_result = await run_janitor_cleanup(dry_run=dry_run)
    results["videos_deleted"] = video_result.get("deleted", 0)

    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE COMPLETE")
    run_logger.info(f"Stats archived: {results.get('stats_archived', 0)}")
    run_logger.info(f"Videos deleted: {results.get('videos_deleted', 0)}")
    run_logger.info("=" * 60)


def main() -> None:
    """Entry point for running the Janitor as a standalone service."""
    try:
        # Run with dry_run=True by default for safety
        asyncio.run(janitor_cycle(dry_run=True))  # type: ignore[arg-type]
    except KeyboardInterrupt:
        logger.info("Janitor stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Janitor failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
