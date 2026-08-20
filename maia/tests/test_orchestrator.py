"""
Tests for Maia Orchestrator (in-process fleet scheduler).

Contract: docs/implementation-checklists/orchestrator-contract.md
Covers: build_specs surface, run_cycle failure isolation / cancellation
pass-through / jitter, agent_loop cadence + first-tick jitter, run dispatch,
signal drain, and _run_until_stop shutdown.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from maia.orchestrator import (
    CycleSpec,
    _run_until_stop,
    agent_loop,
    build_specs,
    main,
    run,
    run_cycle,
)

# Real asyncio.sleep captured at import time (before the autouse mock_sleep
# fixture patches asyncio.sleep) so tests can yield to the event loop.
_REAL_SLEEP = asyncio.sleep

_NINE_AGENTS = {
    "streamer",
    "singer",
    "painter",
    "scribe",
    "hunter",
    "tracker",
    "archeologist",
    "heartbeat",
    "janitor",
}


# --- build_specs: public surface ---


def test_build_specs_returns_nine_agents():
    specs = build_specs()
    assert len(specs) == 9
    assert {s.name for s in specs} == _NINE_AGENTS


def test_build_specs_intervals_match_prefect_yaml():
    specs = {s.name: s for s in build_specs()}
    assert specs["tracker"].interval == 60
    assert specs["streamer"].interval == 120
    assert specs["painter"].interval == 120
    assert specs["scribe"].interval == 120
    assert specs["singer"].interval == 300
    assert specs["hunter"].interval == 300
    assert specs["archeologist"].interval == 600
    assert specs["heartbeat"].interval == 900
    assert specs["janitor"].interval == 900


def test_build_specs_kwargs_match_prefect_yaml():
    specs = {s.name: s for s in build_specs()}
    assert specs["tracker"].kwargs == {"batch_size": 50}
    assert specs["streamer"].kwargs == {"batch_size": 5}
    assert specs["painter"].kwargs == {"batch_size": 5}
    assert specs["scribe"].kwargs == {"batch_size": 10}
    assert specs["singer"].kwargs == {"batch_size": 10}
    assert specs["hunter"].kwargs == {"batch_size": 10}
    assert specs["archeologist"].kwargs == {"start_year": 2010, "end_year": 2024}
    assert specs["heartbeat"].kwargs == {}
    assert specs["janitor"].kwargs == {"dry_run": False}


# --- run_cycle: failure isolation, cancellation, jitter ---


@pytest.mark.asyncio
async def test_run_cycle_logs_completion_on_success():
    coro = AsyncMock(return_value="ok")
    with patch("maia.orchestrator.logger") as mock_logger:
        await run_cycle("tracker", coro())
    coro.assert_awaited_once()
    mock_logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_run_cycle_swallows_failure_and_logs():
    async def boom():
        raise RuntimeError("boom")

    with patch("maia.orchestrator.logger") as mock_logger:
        # A failing cycle must never propagate out of run_cycle.
        await run_cycle("tracker", boom())
    mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_run_cycle_re_raises_cancelled_error():
    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_cycle("tracker", cancel())


@pytest.mark.asyncio
async def test_run_cycle_jitter_sleeps_before_await(mock_sleep):
    coro = AsyncMock(return_value=None)
    await run_cycle("tracker", coro(), jitter=1.5)
    mock_sleep.assert_awaited_once_with(1.5)
    coro.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_cycle_no_jitter_does_not_sleep(mock_sleep):
    coro = AsyncMock(return_value=None)
    await run_cycle("tracker", coro())
    mock_sleep.assert_not_awaited()


# --- agent_loop: cadence + first-tick jitter ---


async def _yielding_flow(**kwargs):
    """Flow stub that yields to the event loop so the infinite loop can be
    driven and cancelled from the test."""
    await _REAL_SLEEP(0)
    return None


@pytest.mark.asyncio
async def test_agent_loop_calls_flow_with_kwargs_and_sleeps_interval(mock_sleep):
    flow = MagicMock(side_effect=_yielding_flow)
    spec = CycleSpec("tracker", flow, interval=60, kwargs={"batch_size": 50})
    task = asyncio.create_task(agent_loop(spec))
    for _ in range(3):
        await _REAL_SLEEP(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    flow.assert_called_with(batch_size=50)
    # First sleep is the stagger jitter; subsequent sleeps are the interval.
    sleeps = [c.args[0] for c in mock_sleep.await_args_list]
    assert sleeps[0] == id(spec) % 13 * 0.6
    assert 60 in sleeps


@pytest.mark.asyncio
async def test_agent_loop_jitter_only_on_first_tick(mock_sleep):
    flow = MagicMock(side_effect=_yielding_flow)
    spec = CycleSpec("painter", flow, interval=120, kwargs={"batch_size": 5})
    task = asyncio.create_task(agent_loop(spec))
    for _ in range(4):
        await _REAL_SLEEP(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    sleeps = [c.args[0] for c in mock_sleep.await_args_list]
    assert sleeps[0] == id(spec) % 13 * 0.6
    # Every sleep after the first must be the plain interval (no jitter).
    assert all(s == 120 for s in sleeps[1:])


# --- run: worker dispatch ---


@pytest.mark.asyncio
async def test_run_dispatches_one_task_per_spec():
    specs = [
        CycleSpec("a", MagicMock(return_value=AsyncMock(return_value=None)), 1),
        CycleSpec("b", MagicMock(return_value=AsyncMock(return_value=None)), 1),
    ]
    with (
        patch("maia.orchestrator.agent_loop", return_value=AsyncMock()),
        patch("maia.orchestrator.asyncio.create_task") as mock_ct,
        patch("maia.orchestrator.asyncio.gather") as mock_gather,
    ):
        mock_gather.return_value = _REAL_SLEEP(0)
        await run(specs)

    assert mock_ct.call_count == 2
    names = [c.kwargs["name"] for c in mock_ct.call_args_list]
    assert names == ["cycle-a", "cycle-b"]


@pytest.mark.asyncio
async def test_run_defaults_to_build_specs():
    with (
        patch("maia.orchestrator.build_specs") as mock_build,
        patch("maia.orchestrator.agent_loop", return_value=AsyncMock()),
        patch("maia.orchestrator.asyncio.create_task") as mock_ct,
        patch("maia.orchestrator.asyncio.gather") as mock_gather,
    ):
        mock_build.return_value = [CycleSpec("x", MagicMock(), 1)]
        mock_gather.return_value = _REAL_SLEEP(0)
        await run()

    mock_build.assert_called_once()
    assert mock_ct.call_count == 1


# --- shutdown: signal drain ---


@pytest.mark.asyncio
async def test_run_until_stop_cancels_runner_when_stop_set():
    stop = asyncio.Event()
    stop.set()
    runner = MagicMock()
    with patch("maia.orchestrator.asyncio.create_task", return_value=runner):
        await _run_until_stop(stop)
    runner.cancel.assert_called_once()


@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
def test_main_installs_signal_handlers_and_drains_pending_tasks():
    loop = MagicMock()
    loop.run_until_complete = MagicMock(return_value=None)
    pending = [MagicMock(), MagicMock()]
    with (
        patch("maia.orchestrator.asyncio.new_event_loop", return_value=loop),
        patch("maia.orchestrator.asyncio.set_event_loop"),
        patch("maia.orchestrator.asyncio.all_tasks", return_value=pending),
        patch("maia.orchestrator.asyncio.gather", return_value=MagicMock()),
        patch("maia.orchestrator.signal.SIGINT", 2, create=True),
        patch("maia.orchestrator.signal.SIGTERM", 15, create=True),
    ):
        main()

    # SIGINT + SIGTERM handlers registered on the loop.
    assert loop.add_signal_handler.call_count == 2
    # In-flight cycles are cancelled (drained) before the loop closes.
    for t in pending:
        t.cancel.assert_called_once()
    loop.close.assert_called_once()
