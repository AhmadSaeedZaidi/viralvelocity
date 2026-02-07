"""Maia Janitor: Tiered storage cleanup agent."""

import argparse
import asyncio
import logging
from typing import Any, Dict

from atlas.adapters.maia import MaiaDAO
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)


@task(name="archive_cold_stats")
async def archive_cold_stats_task(retention_days: int = 7) -> Dict[str, int]:
    """Archive stats older than retention_days from hot tier to cold tier (Vault)."""
    dao = MaiaDAO()
    run_logger = get_run_logger()

    run_logger.info(f"Starting stats archival (retention: {retention_days} days)...")

    total_archived = 0
    batch_count = 0

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

            await asyncio.sleep(1)

        except Exception as e:
            run_logger.error(f"Stats archival failed: {e}")
            raise

    run_logger.info(
        f"Stats archival complete: {total_archived} total rows archived in {batch_count} batches"
    )
    return {"archived": total_archived, "batches": batch_count}


@task(name="run_janitor_cleanup")
async def run_janitor_cleanup_task(dry_run: bool = False) -> Dict[str, Any]:
    """Run the janitor cleanup process."""
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
async def janitor_flow(dry_run: bool = False, archive_stats: bool = True) -> Dict[str, Any]:
    """
    Execute the Janitor cleanup cycle.

    This flow:
    1. Archives old stats from hot tier (SQL) to cold tier (Vault)
    2. Cleans up old processed videos from hot queue

    Args:
        dry_run: Run in dry-run mode without making changes
        archive_stats: Whether to archive stats to cold tier

    Returns:
        Dict with keys: stats_archived, videos_deleted, cleanup_stats
    """
    run_logger = get_run_logger()
    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE STARTING")
    run_logger.info("=" * 60)

    results: Dict[str, Any] = {}

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

    run_logger.info("Phase 2: Cleaning up old videos...")
    video_result = await run_janitor_cleanup_task(dry_run=dry_run)
    results["cleanup_stats"] = video_result
    results["videos_deleted"] = video_result.get("deleted", 0)

    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE COMPLETE")
    run_logger.info(f"Stats archived: {results.get('stats_archived', 0)}")
    run_logger.info(f"Videos deleted: {results.get('videos_deleted', 0)}")
    run_logger.info("=" * 60)

    return results


class JanitorAgent:
    """
    Janitor Agent: Tiered storage cleanup and archival.

    Implements the Agent protocol for polymorphic command dispatch.
    """

    name = "janitor"

    def __init__(self) -> None:
        """Initialize the Janitor agent."""
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Janitor agent."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Run in dry-run mode (default: true)",
        )
        parser.add_argument(
            "--no-dry-run",
            dest="dry_run",
            action="store_false",
            help="Disable dry-run mode (perform actual cleanup)",
        )
        parser.add_argument(
            "--archive-stats",
            action="store_true",
            default=True,
            help="Archive old stats to cold tier (default: true)",
        )
        parser.add_argument(
            "--no-archive-stats",
            dest="archive_stats",
            action="store_false",
            help="Skip stats archival",
        )

    async def run(
        self, dry_run: bool = False, archive_stats: bool = True, **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Execute the Janitor cleanup cycle.

        Args:
            dry_run: Run in dry-run mode without making changes
            archive_stats: Whether to archive stats to cold tier
            **kwargs: Additional arguments (ignored)

        Returns:
            Dict with keys: stats_archived, videos_deleted, cleanup_stats
        """
        return await janitor_flow(dry_run=dry_run, archive_stats=archive_stats)


@flow(name="janitor_cycle")
async def janitor_cycle(dry_run: bool = False, archive_stats: bool = True) -> Dict[str, Any]:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using JanitorAgent directly for new code.
    """
    agent = JanitorAgent()
    return await agent.run(dry_run=dry_run, archive_stats=archive_stats)


@task(name="archive_cold_stats")
async def archive_cold_stats(retention_days: int = 7) -> Any:
    """Legacy function wrapper for backward compatibility."""
    return await archive_cold_stats_task(retention_days)


@task(name="run_janitor_cleanup")
async def run_janitor_cleanup(dry_run: bool = False) -> Any:
    """Legacy function wrapper for backward compatibility."""
    return await run_janitor_cleanup_task(dry_run)


def main() -> None:
    """Entry point for running the Janitor as a standalone service."""
    try:
        agent = JanitorAgent()
        asyncio.run(agent.run(dry_run=True))
    except KeyboardInterrupt:
        logger.info("Janitor stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Janitor failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
