#!/usr/bin/env python3
"""Idempotent setup of the Pleiades Prefect orchestration on the micro control plane.

This captures the orchestration config that lives ONLY in the micro's SQLite
DB — the work-pool global concurrency limit, and the per-deployment work-queue
limits + priorities — so the 2-VPS setup is reproducible after a micro rebuild
or work-pool recreation.

What it does:
  * sets the ``default`` work pool global ``concurrency_limit`` to 9 (one slot
    per queue; the worker's ``CPUQuota=180%`` cgroup + per-queue ``limit=1``
    prevent CPU saturation on the 2-core box)
  * creates/updates one work queue per deployment, each ``concurrency_limit=1``
    so every agent type runs at most one instance at a time

It does NOT touch deployment schedules or ``work_queue_name`` bindings — those
live in ``prefect.yaml`` and are applied by ``prefect deploy``.

SECURITY: contains no secrets. All credentials (``PREFECT_API_URL``, and if the
server requires auth, ``PREFECT_API_KEY``) are read from the environment — the
same env the ``prefect-worker`` systemd unit already exports. Never paste tokens
into this file.

Usage (run on the executor VPS, with the worker's env available):
    export PREFECT_API_URL=http://10.0.0.22:4200/api
    python tools/setup_orchestration.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolUpdate

POOL_NAME = "default"
# Global ceiling for the 2-core executor. Set to the number of queues (9) so
# every agent type gets exactly one slot.  With per-queue `limit=1` the worst
# case is 9 concurrent runs, safe under the worker's CPUQuota=180% cgroup
# backstop (most agents are cheap DB queries; the heavy ffmpeg agents are
# limited to 1 each anyway).
#
# HISTORY: was 5 with strict priorities 1-9, which caused priority starvation:
# heartbeat(1)/janitor(2)/archeologist(3)/tracker(4)/hunter(5) filled the pool
# and streamer(9)/singer(8)/painter(7) never got a slot → no raw videos fetched
# → pipeline stalled.  With limit=9 every queue gets a slot regardless, matching
# the old "all agents run concurrently" model.
GLOBAL_CONCURRENCY_LIMIT = 9

# Per-deployment work queues — same order as docs/micro-prefect-orchestration.md §3.
# Priority is moot when GLOBAL_CONCURRENCY_LIMIT ≥ len(QUEUES) because every
# queue with pending work gets a slot.  Priorities are kept for a visual ordering
# in the Prefect UI; they do not gate scheduling.
QUEUES = [
    ("heartbeat", 1),
    ("janitor", 2),
    ("archeologist", 3),
    ("tracker", 4),
    ("hunter", 5),
    ("scribe", 6),
    ("painter", 7),
    ("singer", 8),
    ("streamer", 9),
]


async def main() -> int:
    api_url = os.environ.get("PREFECT_API_URL")
    if not api_url:
        print(
            "ERROR: PREFECT_API_URL is required (e.g. http://10.0.0.22:4200/api)",
            file=sys.stderr,
        )
        return 2

    async with get_client() as client:
        # --- work pool global limit ---
        try:
            await client.read_work_pool(POOL_NAME)
        except Exception as exc:  # noqa: BLE001
            print(
                f"ERROR: work pool '{POOL_NAME}' not found ({exc}).\n"
                f"       Start the prefect-worker service first (it creates the pool),\n"
                f"       then re-run.",
                file=sys.stderr,
            )
            return 1

        await client.update_work_pool(
            POOL_NAME, work_pool=WorkPoolUpdate(concurrency_limit=GLOBAL_CONCURRENCY_LIMIT)
        )
        print(f"work pool '{POOL_NAME}': concurrency_limit -> {GLOBAL_CONCURRENCY_LIMIT}")

        # --- per-deployment work queues ---
        existing = {q.name: q for q in await client.read_work_queues(work_pool_name=POOL_NAME)}
        for name, priority in QUEUES:
            if name in existing:
                q = existing[name]
                changes = []
                if q.concurrency_limit != 1:
                    changes.append(f"limit {q.concurrency_limit}->1")
                if q.priority != priority:
                    changes.append(f"priority {q.priority}->{priority}")
                await client.update_work_queue(q.id, concurrency_limit=1, priority=priority)
                suffix = f" ({', '.join(changes)})" if changes else " (already correct)"
                print(f"queue '{name}': updated{suffix}")
            else:
                await client.create_work_queue(
                    name=name,
                    concurrency_limit=1,
                    priority=priority,
                    work_pool_name=POOL_NAME,
                )
                print(f"queue '{name}': created (limit=1, priority={priority})")

        # The pre-existing `default` queue is intentionally left as-is (unused;
        # no deployment binds to it). If you want a safety cap there too, add:
        #   await client.update_work_queue(existing['default'].id, concurrency_limit=1)

    print("orchestration setup complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
