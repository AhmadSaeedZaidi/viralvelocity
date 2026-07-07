"""Maia Janitor: Tiered storage state-machine cleanup agent.

Implements a strict transactional State Machine for moving data
from the hot index (Neon PostgreSQL) to the cold tier (Vault).

Lifecycle::

    PENDING ──► PROCESSING ──► PROCESSED ──► ARCHIVED
        │                                                       ▲
        └──► FAILED                                             │
                                                       (Janitor hand-off)

Phases (per cycle):
    1. SWEEP  - Query VideoRepository for PROCESSED records past retention
    2. BATCH  - Group records into manageable chunks
    3. HAND-OFF - Serialize metadata → Parquet → vault (with verification)
    4. PURGE  - Mark ARCHIVED, delete ephemeral hot-tier rows

On vault failure: log to EventRepository, leave hot DB untouched.
"""

import argparse
import asyncio
import logging
from typing import Any

from atlas.events import events
from atlas.repositories import VideoRepository
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 50

# ── Tasks ─────────────────────────────────────────────────────────────────────


@task(name="janitor_sweep")
async def sweep_phase_task(batch_size: int) -> list[dict[str, Any]]:
    """Phase 1: Sweep — find PROCESSED videos eligible for archival."""
    video_repo = VideoRepository()
    run_logger = get_run_logger()

    total = await video_repo.count_archivable()
    if total == 0:
        run_logger.info("Sweep: no PROCESSED videos eligible for archival")
        return []

    run_logger.info(f"Sweep: {total} PROCESSED videos eligible for archival")

    videos = await video_repo.sweep_archivable(batch_size=batch_size)
    return [v.model_dump() for v in videos]


@task(name="janitor_archive_batch")
async def handoff_phase_task(videos_data: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    """Phase 3: Hand-off — serialize each video, vault it, verify, then purge."""
    from atlas.models import Video

    video_repo = VideoRepository()
    run_logger = get_run_logger()

    videos = [Video(**v) for v in videos_data]

    run_logger.info(
        f"Hand-off: archiving {len(videos)} videos (safety_check={dry_run}, dry_run={dry_run})"
    )

    result = await video_repo.archive_video_batch(videos, dry_run=dry_run)

    await events.emit(
        "janitor.batch_complete",
        "janitor",
        {
            "archived": result.get("archived", 0),
            "failed": result.get("failed", 0),
            "failed_ids": result.get("failed_ids", []),
            "dry_run": result.get("dry_run", False),
            "would_archive": result.get("would_archive", 0),
        },
    )

    run_logger.info(
        f"Hand-off: {result.get('archived', 0)} archived, {result.get('failed', 0)} failed"
    )
    return result


@task(name="janitor_archive_stats")
async def archive_cold_stats_task(retention_days: int = 7) -> dict[str, int]:
    """Archive stats_log rows older than retention_days from hot tier to vault."""
    video_repo = VideoRepository()
    run_logger = get_run_logger()

    run_logger.info(f"Stats archival starting (retention: {retention_days} days)...")

    total_archived = 0
    batch_count = 0

    while True:
        try:
            archived = await video_repo.archive_cold_stats(
                retention_days=retention_days, batch_size=5000
            )
            if archived == 0:
                break

            total_archived += archived
            batch_count += 1
            run_logger.info(f"Stats batch {batch_count}: {archived} rows (total: {total_archived})")
            await asyncio.sleep(1)
        except Exception as e:
            run_logger.error(f"Stats archival batch failed: {e}")
            raise

    run_logger.info(f"Stats archival complete: {total_archived} rows in {batch_count} batches")
    return {"archived": total_archived, "batches": batch_count}


@task(name="janitor_log_summary")
async def log_summary_task(results: dict[str, Any]) -> None:
    """Emit final summary event for the janitor cycle."""
    run_logger = get_run_logger()
    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE SUMMARY")
    run_logger.info(f"  Stats archived:       {results.get('stats_archived', 0)}")
    run_logger.info(f"  Videos archived:      {results.get('videos_archived', 0)}")
    run_logger.info(f"  Videos failed:        {results.get('videos_failed', 0)}")
    run_logger.info(f"  Dry run:              {results.get('dry_run', False)}")
    run_logger.info("=" * 60)

    await events.emit(
        "janitor.cycle_complete",
        "janitor",
        {
            "stats_archived": results.get("stats_archived", 0),
            "videos_archived": results.get("videos_archived", 0),
            "videos_failed": results.get("videos_failed", 0),
            "dry_run": results.get("dry_run", False),
        },
    )


# ── Orchestration Flow ────────────────────────────────────────────────────────


@flow(name="janitor_cycle")
async def janitor_flow(
    dry_run: bool = False,
    archive_stats: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """
    Execute the Janitor cleanup cycle — a strict transactional state machine.

    Phases:
    1. **Stats archival** (optional) — move old ``video_stats_log`` rows to vault
    2. **Sweep** — query for ``PROCESSED`` videos past the retention threshold
    3. **Hand-off** — serialize → vault → verify → mark ``ARCHIVED`` → purge
    4. **Summary** — emit final event to ``system_events``

    Args:
        dry_run: Log what would happen without making changes.
        archive_stats: Whether to run stats archival phase.
        batch_size: Number of videos to process per hand-off batch.

    Returns:
        Dict with keys: stats_archived, videos_archived, videos_failed, dry_run.
    """
    run_logger = get_run_logger()
    run_logger.info("=" * 60)
    run_logger.info("JANITOR STATE MACHINE CYCLE STARTING")
    run_logger.info(f"  dry_run={dry_run}, archive_stats={archive_stats}")
    run_logger.info("=" * 60)

    results: dict[str, Any] = {
        "stats_archived": 0,
        "videos_archived": 0,
        "videos_failed": 0,
        "dry_run": dry_run,
    }

    # ── Phase 1: Archive cold stats ───────────────────────────────────────
    if archive_stats and not dry_run:
        run_logger.info("Phase 1/3: Archiving cold stats...")
        try:
            stats_result = await archive_cold_stats_task(retention_days=7)
            results["stats_archived"] = stats_result["archived"]
        except Exception as e:
            run_logger.error(f"Phase 1 (stats) failed: {e}")
            results["stats_error"] = str(e)
    else:
        run_logger.info(f"Phase 1/3: Skipped (archive_stats={archive_stats}, dry_run={dry_run})")

    # ── Phase 2: Sweep ────────────────────────────────────────────────────
    run_logger.info("Phase 2/3: Sweeping for PROCESSED videos...")
    all_archivable: list[dict[str, Any]] = []
    page = await sweep_phase_task.fn(batch_size)
    all_archivable.extend(page)

    remaining = len(page)
    while remaining == batch_size:
        page = await sweep_phase_task.fn(batch_size)
        all_archivable.extend(page)
        remaining = len(page)

    run_logger.info(f"Phase 2/3: Found {len(all_archivable)} videos to archive")

    # ── Phase 3: Hand-off (serialize → vault → verify → purge) ───────────
    if not all_archivable:
        run_logger.info("Phase 3/3: No videos to archive — cycle complete")
        await log_summary_task.fn(results)
        return results

    run_logger.info(f"Phase 3/3: Archiving {len(all_archivable)} videos...")

    # Process in sub-batches for memory control
    sub_batch_size = min(batch_size, 25)
    total_archived = 0
    total_failed = 0

    for i in range(0, len(all_archivable), sub_batch_size):
        sub_batch = all_archivable[i : i + sub_batch_size]
        handoff_result = await handoff_phase_task.fn(sub_batch, dry_run)

        if dry_run:
            total_archived += handoff_result.get("would_archive", 0)
        else:
            total_archived += handoff_result.get("archived", 0)
            total_failed += handoff_result.get("failed", 0)

        run_logger.info(
            f"  Sub-batch {i // sub_batch_size + 1}: "
            f"{handoff_result.get('archived', 0)} archived, "
            f"{handoff_result.get('failed', 0)} failed"
        )

    results["videos_archived"] = total_archived
    results["videos_failed"] = total_failed

    await log_summary_task.fn(results)
    return results


# ── Agent / CLI ───────────────────────────────────────────────────────────────


class JanitorAgent:
    """
    Janitor Agent: Tiered storage state machine.

    Implements the Agent protocol for polymorphic command dispatch.
    """

    name = "janitor"

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
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
            help="Disable dry-run mode (perform actual archival)",
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
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Videos per archival batch (default: {DEFAULT_BATCH_SIZE})",
        )

    async def run(
        self,
        dry_run: bool = False,
        archive_stats: bool = True,
        batch_size: int = DEFAULT_BATCH_SIZE,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = await janitor_flow(
            dry_run=dry_run, archive_stats=archive_stats, batch_size=batch_size
        )
        return result


# ── Legacy wrappers ───────────────────────────────────────────────────────────


@flow(name="janitor_cycle")
async def janitor_cycle(
    dry_run: bool = False, archive_stats: bool = True, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict[str, Any]:
    """Legacy function wrapper for backward compatibility."""
    agent = JanitorAgent()
    return await agent.run(dry_run=dry_run, archive_stats=archive_stats, batch_size=batch_size)


@task(name="archive_cold_stats")
async def archive_cold_stats(retention_days: int = 7) -> Any:
    return await archive_cold_stats_task(retention_days)


@task(name="run_janitor_cleanup")
async def run_janitor_cleanup(dry_run: bool = False) -> Any:
    repo = VideoRepository()
    return await repo.run_janitor(dry_run)


def main() -> None:
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
