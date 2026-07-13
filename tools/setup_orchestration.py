#!/usr/bin/env python3
"""Idempotent setup of the Pleiades Prefect orchestration on the micro control plane.

This captures the orchestration config that lives ONLY in the micro's SQLite
DB — the work-pool global concurrency limit, and the per-deployment work-queue
limits + priorities — so the 2-VPS setup is reproducible after a micro rebuild
or work-pool recreation.

What it does:
  * sets the ``default`` work pool global ``concurrency_limit`` to 3 (CPU-safe
    ceiling for the 2-core executor)
  * creates/updates one work queue per deployment, each ``concurrency_limit=1``,
    with ``heartbeat`` at the highest priority (1) so it never starves

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
# Global ceiling for the 2-core executor. Kept above the number of *cheap* agents so
# trackers/hunters/heartbeat/janitor run concurrently like they used to, while the
# per-queue `limit=1` (plus the worker's CPUQuota=180% cgroup backstop) still prevents
# any CPU death-spiral. Cheap agents rank above the heavy ffmpeg agents in queue priority,
# so they win slots first; heavy agents stay naturally throttled.
GLOBAL_CONCURRENCY_LIMIT = 5

# (queue_name, priority) — heartbeat highest so the Discord heartbeat never starves.
# Order mirrors docs/micro-prefect-orchestration.md §3.
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
