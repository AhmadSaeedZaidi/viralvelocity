"""
Tests for Maia Heartbeat fleet-unit enumeration.
"""

from maia.heartbeat.flow import FLEET_UNITS


def test_fleet_includes_all_scheduled_agents():
    # Producers + consumers that run as systemd units.
    for unit in (
        "pleiades-hunter",
        "pleiades-archeologist",
        "pleiades-scribe",
        "pleiades-painter",
        "pleiades-streamer",
        "pleiades-singer",
        "pleiades-tracker",
        "pleiades-janitor",
        "bgutil-provider",
    ):
        assert unit in FLEET_UNITS, f"{unit} missing from FLEET_UNITS"


def test_fleet_excludes_manual_only_muralist():
    # muralist is a manual-only capability with no systemd unit to probe.
    assert "pleiades-muralist" not in FLEET_UNITS
