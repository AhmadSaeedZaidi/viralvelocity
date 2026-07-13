"""
Tests for Maia Heartbeat fleet-unit enumeration.
"""

from maia.heartbeat.flow import FLEET_DEPLOYMENTS, FLEET_UNITS


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
