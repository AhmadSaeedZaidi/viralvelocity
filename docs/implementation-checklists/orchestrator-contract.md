# Orchestrator Contract — `maia/src/maia/orchestrator.py`

Status: **spec** (grounded in source at commit time; where the code is ambiguous the
contract states the *intended* behavior and flags the discrepancy).
Scope: cleanup-plan item 3(a). Companion tests: `maia/tests/test_orchestrator.py`.

---

## 1. Role & dual-server division

The orchestrator is the **in-process scheduler** for the nine-agent Pleiades fleet. It
replaces the old `prefect worker start --type process` model, which spawned a full
`python -m prefect.engine` subprocess (~17 threads, 170–400 MB RSS) per flow run and blew
the systemd `TasksMax` ceiling.

**Dual-server division** (see `docs/micro-prefect-orchestration.md` §1, §8):

| Host | Role | Runs |
|------|------|------|
| **Control plane (micro)** `e2-micro-server` | Prefect **server/API** on `:4200` (SQLite) | stores deployments, schedules, flow-run state — **not** video data |
| **Executor VPS** `10.0.0.6` | `prefect-orchestrator.service` running `maia.orchestrator` in-process | drives all nine fleet flows on one asyncio loop |

Because `PREFECT_API_URL` is set, Prefect still creates a real flow run in the control
plane (so `get_run_logger()`, telemetry, retries keep working) — but there is **no
subprocess spawn** per cycle. The orchestrator owns the *execution* side; the micro owns
*orchestration state*. The orchestrator never talks to the video DB directly.

---

## 2. Public surface

| Symbol | Kind | Signature | Returns |
|--------|------|-----------|---------|
| `CycleSpec` | `@dataclass` | `(name: str, flow_factory: CoroFactory, interval: float, kwargs: dict = {})` | — |
| `CoroFactory` | type alias | `Callable[..., Any]` | a coroutine |
| `build_specs()` | function | `() -> list[CycleSpec]` | the nine agent specs |
| `run_cycle(name, coro, *, jitter=0.0)` | `async` | `(str, Any, float) -> None` | `None` |
| `agent_loop(spec)` | `async` | `(CycleSpec) -> None` | never returns (infinite) |
| `run(specs=None)` | `async` | `(list[CycleSpec] \| None) -> None` | never returns (infinite) |
| `main()` | function | `() -> None` | `None` |
| `_run_until_stop(stop)` | `async` | `(asyncio.Event) -> None` | `None` |

`build_specs()` mirrors `prefect.yaml` deployments exactly:

| name | flow_factory | interval (s) | kwargs |
|------|--------------|--------------|--------|
| streamer | `streamer_flow` | 120 | `{"batch_size": 5}` |
| singer | `singer_flow` | 300 | `{"batch_size": 10}` |
| painter | `painter_flow` | 120 | `{"batch_size": 5}` |
| scribe | `scribe_flow` | 120 | `{"batch_size": 10}` |
| hunter | `run_hunter_cycle` | 300 | `{"batch_size": 10}` |
| tracker | `run_tracker_cycle` | 60 | `{"batch_size": 50}` |
| archeologist | `run_archeology_campaign` | 600 | `{"start_year": 2010, "end_year": 2024}` |
| heartbeat | `heartbeat_flow` | 900 | `{}` |
| janitor | `janitor_flow` | 900 | `{"dry_run": False}` |

---

## 3. Responsibilities (owned by the orchestrator)

1. **Scheduling loop** — `agent_loop` runs one agent forever: `run_cycle(...)` then
   `asyncio.sleep(spec.interval)`. The interval is measured **from cycle start** (sleep
   happens *after* the cycle completes), so a slow cycle does not drift the cadence.
2. **Worker dispatch** — `run` creates exactly **one asyncio task per spec**
   (`name=f"cycle-{spec.name}"`) and `asyncio.gather`s them all on the single loop.
3. **Failure isolation** — `run_cycle` wraps the awaited coroutine in
   `try/except Exception` and **logs** (`orchestrator cycle <name> FAILED`) instead of
   raising. A single failing cycle must never stall or kill the loop.
4. **Cancellation pass-through** — `asyncio.CancelledError` is **re-raised** (not
   swallowed) so shutdown can cancel in-flight cycles and let Prefect mark runs
   `CANCELLING`.
5. **Jitter stagger** — each `agent_loop` computes a first-tick jitter
   (`id(spec) % 13 * 0.6` seconds) so the fleet does not fire all at once after a
   restart (avoids a cold-start burst on the control plane + DB). Jitter applies **only
   to the first tick**; subsequent ticks sleep the plain interval.
6. **Signal drain** — `main` installs `SIGINT`/`SIGTERM` handlers that set a stop event;
   `_run_until_stop` cancels the runner task; `main` then cancels all pending tasks and
   drains them with `asyncio.gather(..., return_exceptions=True)` before closing the loop.
7. **Error/retry policy** — the orchestrator does **no retry**. A failed cycle is logged
   and the next cycle runs at the next interval. Retry/backoff is delegated to the agents
   (e.g. hunter/archeologist swallow YouTube daily-quota errors and retry next interval;
   streamer backs off via `atlas.state`).

### Responsibility states

The orchestrator has **no explicit state machine** — states are emergent from the loop:

| State | Meaning | Behavior |
|-------|---------|----------|
| **idle** | between cycles, in `asyncio.sleep(interval)` | loop is parked; no work dispatched |
| **running** | `await coro` in `run_cycle` | one agent's flow is executing in-process |
| **failed** | coroutine raised `Exception` | caught + logged by `run_cycle`; loop continues |
| **cap-reaching** | agent raised `QuotaExhaustedError` (or similar) | treated as a normal failure by `run_cycle`; retry deferred to the agent's own policy on the next interval |

> **Discrepancy note:** the task brief asked for explicit "state transitions
> (idle/running/failed/cap-reaching)". The source has **no state object** — these are
> documented here as the intended emergent semantics, and the tests assert the observable
> behaviors (sleep between cycles, exception swallowed, cancellation propagated) rather
> than a nonexistent state attribute.

---

## 4. NON-responsibilities (delegated to other modules)

The orchestrator **does not**:

- **Touch the DB** — it never opens a connection or runs SQL. All persistence is via the
  agents' repositories (`atlas.repositories.*`), which the orchestrator only invokes
  through the flow entrypoints. (Tests therefore mock the flow factories; no
  FakeDriver/DB is needed at the orchestrator layer.)
- **Manage the watchlist** — `WatchlistRepository` tier decay / `calculate_next_track_time`
  / `update_schedule` are owned by the **tracker**.
- **Stage transcripts** — owned by the **scribe**.
- **Write to the vault** — owned by the **janitor** (`vault_flush_task`).
- **Report fleet health** — the **heartbeat** agent owns `collect_fleet_status`; the
  orchestrator merely schedules `heartbeat_flow` as one of its nine cycles.
- **Enforce per-agent concurrency** — that is the work-queue `concurrency_limit=1` +
  pool `concurrency_limit=9` topology on the micro (see runbook §3), not the orchestrator.
- **Retry** — see §3.7.

---

## 5. Interaction with the DB / collaborators

The orchestrator's only collaborators are the nine `flow_factory` callables. It calls
`spec.flow_factory(**spec.kwargs)` to obtain a coroutine and awaits it. It holds **no
repository, no client, no connection**. This makes it trivially unit-testable: mock the
flow factories (return `AsyncMock` coroutines) and assert scheduling/dispatch/isolation
behavior without any DB. There is no FakeDriver at this layer because the orchestrator
never reaches the DB — the FakeDriver convention applies to the agents' repository tests.

---

## 6. Test contract (what `test_orchestrator.py` must prove)

1. `build_specs()` returns exactly the nine agents with the documented intervals/kwargs.
2. `run_cycle` logs completion on success, swallows+logs a generic failure, re-raises
   `CancelledError`, and sleeps `jitter` before awaiting when jitter is nonzero.
3. `agent_loop` calls the flow factory with the spec kwargs, sleeps the interval after
   each cycle, and applies jitter only on the first tick.
4. `run` dispatches exactly one task per spec with `cycle-<name>` names.
5. `_run_until_stop` cancels the runner once the stop event is set.
6. `main` installs SIGINT/SIGTERM handlers and drains pending tasks on shutdown.