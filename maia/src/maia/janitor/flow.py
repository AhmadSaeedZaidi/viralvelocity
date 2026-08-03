"""Maia Janitor: tiered storage state-machine cleanup agent.

Moves data from the hot index (Neon PostgreSQL) to the cold tier (Vault) via a
strict transactional state machine: PENDING → PROCESSING → PROCESSED → ARCHIVED
(and FAILED). On vault failure it logs to EventRepository and leaves the hot DB
untouched.
"""

import argparse
import asyncio
import io
import logging
import os
from typing import Any

from atlas.events import events
from atlas.notifications import AlertChannel, AlertLevel, notifier
from atlas.repositories import TranscriptRepository, VideoRepository
from atlas.vault import get_vault, transcript_path
from prefect import flow, get_run_logger, task

from maia.utils import cli_bootstrap, run_agent_main

logger = logging.getLogger(__name__)


DEFAULT_BATCH_SIZE = 50


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
        f"Hand-off: archiving {len(videos)} videos (dry_run={dry_run})"
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
    return result  # type: ignore[no-any-return]


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
            run_logger.exception(f"Stats archival batch failed: {e}")
            raise

    run_logger.info(f"Stats archival complete: {total_archived} rows in {batch_count} batches")
    return {"archived": total_archived, "batches": batch_count}


@task(name="janitor_refresh_key_pools")
async def refresh_key_pools_task() -> dict[str, Any]:
    """Recompute the dynamic key-pool allocation from the corpus size.

    Gated to a weekly cadence: ``refresh_allocation`` only rewrites the cache
    when older than ``REFRESH_INTERVAL_DAYS``. Hunter/tracking ring sizes then
    scale with the number of videos in the database.
    """
    from atlas.config import get_settings
    from atlas.key_pool import refresh_allocation

    run_logger = get_run_logger()
    settings = get_settings()
    repo = VideoRepository()

    video_count = await repo.count_videos()
    total_keys = len(settings.api_keys)

    sizes = await asyncio.to_thread(
        refresh_allocation,
        total_keys,
        video_count,
        settings.KEY_POOL_ARCHEOLOGY_SIZE,
    )

    if sizes is None:
        run_logger.info("Key-pool allocation still fresh — no change")
        return {"refreshed": False, "video_count": video_count}

    run_logger.info(
        f"Key-pool allocation updated: tracking={sizes.tracking}, "
        f"archeology={sizes.archeology}, video_count={video_count}"
    )
    return {
        "refreshed": True,
        "video_count": video_count,
        "tracking": sizes.tracking,
        "archeology": sizes.archeology,
    }


@task(name="janitor_cull_search_queue")
async def cull_search_queue_task() -> dict[str, Any]:
    """Delete search terms whose time-decayed score has dropped below the cull
    threshold (Phase 2). In-progress paginations are protected."""
    from atlas.repositories import SearchQueueRepository

    run_logger = get_run_logger()
    deleted = await SearchQueueRepository().cull_stale()
    if deleted:
        run_logger.info(f"Search queue: culled {deleted} stale term(s)")
    return {"culled": deleted}


@task(name="janitor_purge_prefect_runs")
async def purge_prefect_runs_task(
    age_days: int = 7,
    batch_limit: int = 50,
    max_batches: int = 20,
) -> dict[str, Any]:
    """Delete flow runs (and cascaded task runs + logs) older than *age_days*.

    Calls Prefect's ``/flow_runs/bulk_delete`` in batches (max 50 per call).
    Single-ownership assumption: the Janitor is the *only* agent that purges
    orchestration history, so there is no TOCTOU concern.

    Returns the total number of flow runs deleted.
    """
    import httpx
    from datetime import datetime, timezone, timedelta

    run_logger = get_run_logger()
    client = httpx.AsyncClient(
        base_url=os.environ.get("PREFECT_API_URL", "http://10.0.0.22:4200/api"),
        headers={"Content-Type": "application/json"},
        timeout=httpx.Timeout(30.0),
    )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    total_deleted = 0
    for batch_idx in range(max_batches):
        resp = await client.post(
            "/flow_runs/bulk_delete",
            json={
                "flow_runs": {
                    "end_time": {"before_": cutoff},
                },
                "limit": batch_limit,
            },
        )
        if resp.status_code != 200:
            run_logger.warning(
                f"Purge batch {batch_idx}: HTTP {resp.status_code} — {resp.text[:200]}"
            )
            break
        body = resp.json()
        deleted = body.get("deleted", [])
        if not deleted:
            break
        total_deleted += len(deleted)

    await client.aclose()

    if total_deleted:
        run_logger.info(f"Purged {total_deleted} completed flow runs older than {age_days}d")
    return {"purged_flow_runs": total_deleted}


@task(name="janitor_reap_zombie_runs")
async def reap_zombie_runs_task(max_age_minutes: int = 15) -> dict[str, Any]:
    """Crash flow runs stuck in RUNNING far longer than any legitimate cycle.

    Each agent's work queue has ``concurrency_limit=1``. If a run's process dies
    without reporting a terminal state (worker restart/SIGKILL, OOM, or a hung
    yt-dlp download killed by systemd), the server leaves it RUNNING forever and
    that single slot is never released — freezing the entire queue (the classic
    "streamer stalled" symptom). Prefect's heartbeat/Foreman zombie detection
    depends on the events websocket, which is unreliable on this 2-VPS split, so
    this is a deterministic backstop: any run RUNNING longer than
    *max_age_minutes* (well beyond the ~1-2min a real cycle takes) is force-set
    to CRASHED, freeing its slot. Idempotent and safe — all agent cycles are
    short, so a legitimate run never approaches the threshold.
    """
    from datetime import datetime, timezone

    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterState
    from prefect.client.schemas.objects import StateType
    from prefect.states import Crashed

    run_logger = get_run_logger()
    reaped = 0
    async with get_client() as client:
        running = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                state=FlowRunFilterState(type={"any_": [StateType.RUNNING]})
            ),
            limit=100,
        )
        now = datetime.now(timezone.utc)
        for r in running:
            started = r.start_time or now
            age_min = (now - started).total_seconds() / 60
            if age_min > max_age_minutes:
                await client.set_flow_run_state(
                    r.id,
                    Crashed(
                        message=(
                            f"Reaped by janitor: RUNNING for {age_min:.0f}min "
                            f"(> {max_age_minutes}min) — process presumed dead, "
                            f"freeing the '{r.work_queue_name}' queue slot."
                        )
                    ),
                    force=True,
                )
                run_logger.warning(
                    f"Reaped zombie run '{r.name}' (queue={r.work_queue_name}, "
                    f"age={age_min:.0f}min)"
                )
                reaped += 1
    if reaped:
        run_logger.warning(f"Janitor reaped {reaped} zombie run(s), freeing queue slots")
    return {"reaped": reaped}


@task(name="janitor_vault_flush")
async def vault_flush_task(batch_size: int = 50) -> dict[str, Any]:
    """Flush staged transcripts (+ audio) from the DB to the vault.

    Single owner of vault writes (Option A): batches every pending video into
    one vault commit, retries on HTTP 429, and is idempotent so it self-heals
    videos left ``vault_write_pending`` by a prior failure.
    """
    transcript_repo = TranscriptRepository()
    run_logger = get_run_logger()

    pending = await transcript_repo.claim_vault_pending_batch(batch_size)
    if not pending:
        return {"flushed": 0, "failed": 0}

    v = get_vault()
    loop = asyncio.get_running_loop()
    flushed = 0
    failed = 0
    # Batch MANY videos into a single HF commit (store_batch retries 429
    # internally), keeping us under HuggingFace's 128-commits/hour account cap.
    CHUNK = 25  # videos per commit (audio is large — stay within commit-size limits)
    groups: list[list[tuple[str, Any]]] = []
    vids: list[str] = []
    for row in pending:
        vid = row["id"]
        vids.append(vid)
        items: list[tuple[str, Any]] = [(transcript_path(vid), row["transcript"])]
        if row.get("audio"):
            items.append((f"audio/{vid}.opus", io.BytesIO(row["audio"])))
        groups.append(items)

    for i in range(0, len(groups), CHUNK):
        chunk_groups = groups[i : i + CHUNK]
        chunk_vids = vids[i : i + CHUNK]
        chunk_items = [it for g in chunk_groups for it in g]
        try:
            # store_batch runs synchronously in a worker thread (non-blocking)
            # and retries 429 internally.
            uris = await loop.run_in_executor(None, v.store_batch, chunk_items)
            for vid in chunk_vids:
                tpath = transcript_path(vid)
                uri = next((u for u in uris if u.endswith(tpath)), None)
                await transcript_repo.clear_vault_pending(vid, uri)
            flushed += len(chunk_vids)
        except Exception as e:  # noqa: BLE001 - surface, don't abort the batch
            failed += len(chunk_vids)
            run_logger.exception(f"Vault flush chunk failed ({len(chunk_vids)} vids): {e}")

    run_logger.info(f"Vault flush: {flushed} flushed, {failed} failed (/ {len(pending)} pending)")
    return {"flushed": flushed, "failed": failed}


@task(name="janitor_log_summary")
async def log_summary_task(results: dict[str, Any]) -> None:
    """Emit final summary event for the janitor cycle."""
    run_logger = get_run_logger()
    run_logger.info("=" * 60)
    run_logger.info("JANITOR CYCLE SUMMARY")
    run_logger.info(f"  Stats archived:       {results.get('stats_archived', 0)}")
    run_logger.info(f"  Videos archived:      {results.get('videos_archived', 0)}")
    run_logger.info(f"  Videos failed:        {results.get('videos_failed', 0)}")
    run_logger.info(f"  Prefect runs purged:  {results.get('purged', 0)}")
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


@flow(name="janitor_cycle")
async def janitor_flow(
    dry_run: bool = False,
    archive_stats: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Execute the Janitor cleanup cycle — a strict transactional state machine.

    Phases: stats archival (optional), sweep for PROCESSED videos, hand-off
    (serialize → vault → verify → mark ARCHIVED → purge), and a summary event.

    Args:
        dry_run: Log what would happen without making changes.
        archive_stats: Whether to run stats archival phase.
        batch_size: Number of videos to process per hand-off batch.

    Returns a dict with stats_archived, videos_archived, videos_failed, dry_run.
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

    try:
        reap_result = await reap_zombie_runs_task()
        results["zombie_runs_reaped"] = reap_result.get("reaped", 0)
    except Exception as e:
        run_logger.exception(f"Phase 0 (zombie-run reap) failed: {e}")
        results["zombie_reap_error"] = str(e)

    try:
        pool_result = await refresh_key_pools_task()
        results["key_pool"] = pool_result
    except Exception as e:
        run_logger.exception(f"Phase 0 (key-pool refresh) failed: {e}")
        results["key_pool_error"] = str(e)

    try:
        cull_result = await cull_search_queue_task()
        results["search_queue_culled"] = cull_result.get("culled", 0)
    except Exception as e:
        run_logger.exception(f"Phase 0b (search-queue cull) failed: {e}")
        results["search_queue_cull_error"] = str(e)

    try:
        flush_result = await vault_flush_task.fn(batch_size)
        results["vault_flushed"] = flush_result.get("flushed", 0)
        results["vault_failed"] = flush_result.get("failed", 0)
    except Exception as e:
        run_logger.exception(f"Phase 0c (vault flush) failed: {e}")
        results["vault_flush_error"] = str(e)

    try:
        purge_result = await purge_prefect_runs_task(age_days=7)
        results["purged"] = purge_result.get("purged_flow_runs", 0)
    except Exception as e:
        run_logger.exception(f"Phase 0d (prefect purge) failed: {e}")
        results["purge_error"] = str(e)

    if archive_stats and not dry_run:
        run_logger.info("Phase 1/3: Archiving cold stats...")
        try:
            stats_result = await archive_cold_stats_task(retention_days=7)
            results["stats_archived"] = stats_result["archived"]
        except Exception as e:
            run_logger.exception(f"Phase 1 (stats) failed: {e}")
            results["stats_error"] = str(e)
    else:
        run_logger.info(f"Phase 1/3: Skipped (archive_stats={archive_stats}, dry_run={dry_run})")

    run_logger.info("Phase 2/3: Sweeping for PROCESSED videos...")
    # Sweep a single page per cycle. sweep_archivable() has no cursor/offset and
    # only marks rows ARCHIVED in the hand-off phase below, so re-calling it in a
    # loop returns the *same* rows forever (infinite loop once the backlog is
    # >= batch_size). The janitor runs periodically, so any remaining backlog is
    # drained by subsequent cycles as rows transition PROCESSED -> ARCHIVED.
    all_archivable: list[dict[str, Any]] = await sweep_phase_task.fn(batch_size)

    run_logger.info(f"Phase 2/3: Found {len(all_archivable)} videos to archive")

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

    await notifier.send(
        title="Janitor Cycle Summary",
        description=(
            f"Archived: {results.get('videos_archived', 0)} videos | "
            f"Stats: {results.get('stats_archived', 0)} rows | "
            f"Vault flushed: {results.get('vault_flushed', 0)} | "
            f"Vault failed: {results.get('vault_failed', 0)} | "
            f"Prefect runs purged: {results.get('purged', 0)}"
        ),
        channel=AlertChannel.ALERTS,
        level=AlertLevel.CRITICAL if results.get('videos_failed', 0) > 0 else AlertLevel.INFO,
        fields={
            "Videos Archived": str(results.get("videos_archived", 0)),
            "Videos Failed": str(results.get("videos_failed", 0)),
            "Stats Archived (rows)": str(results.get("stats_archived", 0)),
            "Vault Flushed": str(results.get("vault_flushed", 0)),
            "Vault Failed": str(results.get("vault_failed", 0)),
            "Search Queue Culled": str(results.get("search_queue_culled", 0)),
            "Zombie Runs Reaped": str(results.get("zombie_runs_reaped", 0)),
            "Prefect Runs Purged": str(results.get("purged", 0)),
            "Dry Run": str(results.get("dry_run", False)),
        },
    )

    return results


class JanitorAgent:
    """Janitor Agent: tiered storage state machine."""

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


@flow(name="janitor_cycle")
async def janitor_cycle(
    dry_run: bool = False, archive_stats: bool = True, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict[str, Any]:
    """Legacy function wrapper for backward compatibility."""
    agent = JanitorAgent()
    return await agent.run(dry_run=dry_run, archive_stats=archive_stats, batch_size=batch_size)


def main() -> None:
    run_agent_main(lambda: JanitorAgent().run(dry_run=True), "janitor")


if __name__ == "__main__":
    cli_bootstrap()
    main()
