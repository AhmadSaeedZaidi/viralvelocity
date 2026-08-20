"""In-process Prefect orchestrator for the Pleiades fleet.

Replaces the scheduler + ``prefect worker start`` ``--type process`` model,
where every flow cycle spawned a full ``python -m prefect.engine`` subprocess
(~17 threads, 170-400 MB RSS each). With many flows that churned past the
systemd ``TasksMax`` ceiling, causing ``RuntimeError: can't start new thread``.

This service invokes each deployment's ``@flow`` entrypoint *in-process* on a
single event loop. Because ``PREFECT_API_URL`` is set, Prefect still creates a
real flow run in the control plane, so ``get_run_logger()``, telemetry, and
logging all work — but there is **no subprocess spawn** per cycle.

To avoid double-execution, the Prefect deployment schedules must be paused.
Rollback: unpause the nine deployments (see docs/micro-prefect-orchestration.md).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# In-process flow entrypoints — identical to prefect.yaml deployments.
from maia.archeologist.flow import run_archeology_campaign  # noqa: F401
from maia.heartbeat.flow import heartbeat_flow  # noqa: F401
from maia.hunter.flow import run_hunter_cycle  # noqa: F401
from maia.janitor.flow import janitor_flow  # noqa: F401
from maia.painter.flow import painter_flow  # noqa: F401
from maia.scribe.flow import scribe_flow  # noqa: F401
from maia.singer.flow import singer_flow  # noqa: F401
from maia.streamer.flow import streamer_flow  # noqa: F401
from maia.tracker.flow import run_tracker_cycle  # noqa: F401

logger = logging.getLogger("maia.orchestrator")

CoroFactory = Callable[..., Any]


@dataclass
class CycleSpec:
    """Declarative definition of one agent's periodic in-process cycle."""

    name: str
    flow_factory: CoroFactory
    interval: float
    kwargs: dict[str, Any] = field(default_factory=dict)


def build_specs() -> list[CycleSpec]:
    """Mirror the deployments defined in ``prefect.yaml`` (params + intervals)."""
    return [
        # (name, flow_fn, interval_seconds, kwargs)
        CycleSpec("streamer", streamer_flow, 120, {"batch_size": 5}),
        CycleSpec("singer", singer_flow, 300, {"batch_size": 10}),
        CycleSpec("painter", painter_flow, 120, {"batch_size": 5}),
        CycleSpec("scribe", scribe_flow, 120, {"batch_size": 10}),
        CycleSpec("hunter", run_hunter_cycle, 300, {"batch_size": 10}),
        CycleSpec("tracker", run_tracker_cycle, 60, {"batch_size": 50}),
        CycleSpec(
            "archeologist",
            run_archeology_campaign,
            600,
            {"start_year": 2010, "end_year": 2024},
        ),
        CycleSpec("heartbeat", heartbeat_flow, 900, {}),
        CycleSpec("janitor", janitor_flow, 900, {"dry_run": False}),
    ]


async def run_cycle(name: str, coro: Any, *, jitter: float = 0.0) -> None:
    """Await one flow cycle, catching so a single failure never stalls the loop."""
    if jitter:
        await asyncio.sleep(jitter)
    try:
        result = await coro
        logger.info("orchestrator cycle %s complete: %s", name, result)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a failing cycle must not kill the loop
        logger.exception("orchestrator cycle %s FAILED", name)


async def agent_loop(spec: CycleSpec) -> None:
    """Run one agent forever at ``spec.interval`` (measured from cycle start)."""
    # Stagger each agent's first tick so the fleet does not fire all at once
    # after a restart (avoid a cold-start burst on control plane + DB).
    jitter = id(spec) % 13 * 0.6
    logger.info(
        "starting orchestrator cycle %s (interval=%ss, kwargs=%s)",
        spec.name,
        spec.interval,
        spec.kwargs,
    )
    while True:
        await run_cycle(spec.name, spec.flow_factory(**spec.kwargs), jitter=jitter)
        jitter = 0.0
        await asyncio.sleep(spec.interval)


async def run(specs: list[CycleSpec] | None = None) -> None:
    """Launch one asyncio task per agent cycle loop, all in the same process."""
    spec_list = specs or build_specs()
    logger.info("starting orchestrator with %d agents", len(spec_list))
    tasks = [
        asyncio.create_task(agent_loop(spec), name=f"cycle-{spec.name}")
        for spec in spec_list
    ]
    await asyncio.gather(*tasks)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s - %(message)s",
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop = asyncio.Event()

    def _request_stop(signum: int, _frame: Any) -> None:
        logger.warning("received signal %s; draining", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop, sig, None)
        except NotImplementedError:  # non-POSIX
            signal.signal(sig, lambda s, f: stop.set())

    try:
        loop.run_until_complete(_run_until_stop(stop))
    finally:
        # Cancel in-flight cycles, letting Prefect mark the runs CANCELLING.
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


async def _run_until_stop(stop: asyncio.Event) -> None:
    runner = asyncio.create_task(run())
    await stop.wait()
    runner.cancel()


if __name__ == "__main__":
    main()
