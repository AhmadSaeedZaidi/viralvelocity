"""
Tests for Maia Heartbeat fleet-unit enumeration.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from maia.heartbeat.flow import (
    _RUN_STATE_HEALTH,
    FLEET_DEPLOYMENTS,
    FLEET_UNITS,
    _unit_state,
    collect_fleet_status,
)


def _deployment(name):
    d = MagicMock()
    d.id = uuid4()
    d.name = name
    return d


def test_fleet_unit_is_prefect_worker():
    # After the two-VPS migration the nine polling agents are Prefect
    # deployments executed by a single `prefect-worker`, so that is the only
    # systemd unit probed.
    assert FLEET_UNITS == ["prefect-worker"]


def test_fleet_deployments_are_the_nine_agents():
    # The heartbeat now reports all nine automated Prefect deployments.
    assert set(FLEET_DEPLOYMENTS) == {
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


def test_fleet_excludes_manual_only_muralist():
    # muralist is a manual-only capability with no deployment to probe.
    assert "muralist" not in FLEET_DEPLOYMENTS


# --- Run-state health / staleness thresholds ---


@pytest.mark.parametrize(
    "state, expected",
    [
        ("Completed", "healthy"),
        ("Running", "healthy"),
        ("Pending", "warn"),
        ("Scheduled", "warn"),
        ("Paused", "warn"),
        ("Cancelled", "warn"),
        ("Failed", "down"),
        ("Crashed", "down"),
    ],
)
def test_run_state_health_mapping(state, expected):
    """Every Prefect run-state maps to a governed fleet-health label."""
    assert _RUN_STATE_HEALTH[state] == expected


def test_unknown_run_state_falls_back_to_warn():
    """An unrecognised (e.g. future) run-state is treated as a staleness warn,
    not down — a new state must never flip a deployment to down."""
    assert _RUN_STATE_HEALTH.get("some_new_state", "warn") == "warn"


@pytest.mark.asyncio
async def test_collect_fleet_status_stale_never_run_as_warn():
    """A deployment that has never run is reported as 'never run' / warn."""
    deploy = _deployment("tracker")

    client = MagicMock()
    client.read_deployments = AsyncMock(return_value=[deploy])
    client.read_flow_runs = AsyncMock(return_value=[])

    with patch("maia.heartbeat.flow.get_client", return_value=_AsyncCtx(client)):
        status = await collect_fleet_status()

    assert status["tracker"] == ("never run", "warn")


@pytest.mark.asyncio
async def test_collect_fleet_status_marks_unregistered_as_down():
    """A known deployment absent from the API response is 'not registered' / down."""
    deploy = _deployment("hunter")
    client = MagicMock()
    client.read_deployments = AsyncMock(return_value=[deploy])
    client.read_flow_runs = AsyncMock(
        return_value=[]
    )  # not reached for the missing one

    with patch("maia.heartbeat.flow.get_client", return_value=_AsyncCtx(client)):
        status = await collect_fleet_status()

    # hunter is present; every other FLEET_DEPLOYMENT is missing -> down.
    assert status["hunter"][1] == "warn"
    assert status["scribe"] == ("not registered", "down")


@pytest.mark.asyncio
async def test_collect_fleet_status_last_run_state_drives_health():
    """The most recent flow-run's state name drives the deployment's health."""
    deploy = _deployment("janitor")
    run = MagicMock()
    run.state = MagicMock()
    run.state.name = "Failed"
    client = MagicMock()
    client.read_deployments = AsyncMock(return_value=[deploy])
    client.read_flow_runs = AsyncMock(return_value=[run])

    with patch("maia.heartbeat.flow.get_client", return_value=_AsyncCtx(client)):
        status = await collect_fleet_status()

    assert status["janitor"] == ("last run: Failed", "down")


class _AsyncCtx:
    """Async-context-manager shim for ``async with get_client()``."""

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_collect_fleet_status_api_unreachable_marks_all_down():
    """If the Prefect API is unreachable, every deployment is 'API unreachable'"""
    with patch(
        "maia.heartbeat.flow.get_client", side_effect=RuntimeError("no control plane")
    ):
        status = await collect_fleet_status()
    assert status == {
        nm: ("API unreachable", "down") for nm in FLEET_DEPLOYMENTS
    }


@pytest.mark.parametrize(
    "props, expected_health",
    [
        ("ActiveState=active", "healthy"),
        ("ActiveState=activating\nSubState=auto-restart\nExecMainStatus=0", "healthy"),
        ("ActiveState=activating\nSubState=auto-restart\nExecMainStatus=1", "warn"),
        ("ActiveState=activating\nSubState=starting", "warn"),
        ("ActiveState=failed\nExecMainStatus=127", "down"),
    ],
)
def test_unit_state_health(monkeypatch, props, expected_health):
    """systemctl probe output maps to fleet-health labels."""
    with patch(
        "maia.heartbeat.flow.subprocess.run",
        return_value=MagicMock(stdout=props, returncode=0),
    ):
        _label, health = _unit_state("prefect-worker")
    assert health == expected_health


@patch("maia.heartbeat.flow.subprocess.run", side_effect=OSError("no systemctl"))
def test_unit_state_probe_error_is_down(mock_run):
    """A failed probe must never crash the cycle; reports down."""
    _label, health = _unit_state("prefect-worker")
    assert health == "down"
