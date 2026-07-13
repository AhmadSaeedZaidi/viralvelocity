"""Maia Heartbeat: periodic fleet online-status reporter.

Collects service liveness + pipeline metrics once per cycle, posts a status
embed to Discord, and exits (systemd restarts it on its timer).
"""

import argparse
import asyncio
import logging
import subprocess
from typing import Any

import httpx
from atlas.repositories import VideoRepository
from atlas.state import quota_exhausted_agents
from prefect import flow
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterDeploymentId

logger = logging.getLogger(__name__)

# Fleet topology after the two-VPS migration: the nine polling agents are Prefect
# deployments executed by a single long-running `prefect-worker` process. The
# heartbeat therefore reports BOTH (a) the executor worker liveness (systemd
# unit) and (b) the health of every deployment (last flow-run state via the
# Prefect API). `FLEET_UNITS` is the set of systemd units probed for liveness;
# `FLEET_DEPLOYMENTS` is the set of Prefect deployments surfaced in the report.
FLEET_UNITS = [
    "prefect-worker",
]

# All nine automated deployments (muralist is intentionally excluded — it is a
# manual-only capability with no deployment).
FLEET_DEPLOYMENTS = [
    "streamer",
    "singer",
    "painter",
    "scribe",
    "hunter",
    "tracker",
    "archeologist",
    "heartbeat",
    "janitor",
]

# Map a Prefect flow-run state name to fleet health.
_RUN_STATE_HEALTH = {
    "Completed": "healthy",
    "Running": "healthy",
    "Pending": "warn",
    "Scheduled": "warn",
    "Paused": "warn",
    "Cancelled": "warn",
    "Failed": "down",
    "Crashed": "down",
}


def _unit_state(unit: str) -> tuple[str, str]:
    """Return ``(label, health)`` for *unit* (best-effort, no sudo).

    ``health`` is ``healthy`` / ``warn`` / ``down``. The polling agents are
    oneshot loops: they run a short cycle, exit 0, then sit in
    ``activating (auto-restart)`` until the next ``RestartSec`` tick — a *healthy*
    steady state. Only a non-zero last exit or a ``failed`` unit is a real problem.
    """
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "ActiveState,SubState,ExecMainStatus"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        props = dict(
            line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line
        )
        active = props.get("ActiveState", "unknown")
        sub = props.get("SubState", "")
        exit_code = props.get("ExecMainStatus", "0")

        if active == "active":
            return ("active", "healthy")
        if active == "activating" and sub == "auto-restart":
            # Steady state for oneshot-loop agents between RestartSec ticks.
            if exit_code == "0":
                return ("cycling", "healthy")
            return (f"restarting (exit {exit_code})", "warn")
        if active == "activating":
            return ("starting", "warn")
        if active == "failed" or exit_code != "0":
            return (f"failed (exit {exit_code})", "down")
        return (active or "unknown", "down")
    except Exception as e:  # noqa: BLE001 - status probe must never crash the cycle
        logger.warning(f"Could not probe {unit}: {e}")
        return ("unknown", "down")


def collect_service_status() -> dict[str, tuple[str, str]]:
    """Probe every fleet unit and return ``{unit: (label, health)}``."""
    return {unit: _unit_state(unit) for unit in FLEET_UNITS}


async def collect_fleet_status() -> dict[str, tuple[str, str]]:
    """Return ``{deployment: (label, health)}`` from the Prefect API.

    Health is derived from each deployment's most recent flow run. Best-effort:
    if the API is unreachable, every deployment is reported as unreachable so
    the operator can see the control plane is down (rather than the agents).

    Note: the micro's Prefect server caps ``read_flow_runs`` at ``limit=200``
    (it returns ``422 Unprocessable Entity`` above that). Querying each
    deployment's latest run individually (``limit=1``) sidesteps the cap
    entirely and is always correct regardless of run volume.
    """
    out: dict[str, tuple[str, str]] = {}
    try:
        async with get_client() as client:
            deployments = await client.read_deployments(limit=100)
            for d in deployments:
                try:
                    runs = await client.read_flow_runs(
                        flow_run_filter=FlowRunFilter(
                            deployment_id=FlowRunFilterDeploymentId(any_=[d.id])
                        ),
                        limit=1,
                    )
                except Exception:  # noqa: BLE001 - one bad deployment must not sink the rest
                    runs = []
                if runs and runs[0].state:
                    st = runs[0].state.name
                    out[d.name] = (f"last run: {st}", _RUN_STATE_HEALTH.get(st, "warn"))
                else:
                    out[d.name] = ("never run", "warn")
            # Surface any known deployment missing from the API response.
            for nm in FLEET_DEPLOYMENTS:
                if nm not in out:
                    out[nm] = ("not registered", "down")
    except Exception as e:  # noqa: BLE001 - fleet probe must never crash the cycle
        logger.warning(f"Could not query Prefect fleet status: {e}")
        out = {nm: ("API unreachable", "down") for nm in FLEET_DEPLOYMENTS}
    return out


async def collect_pipeline_metrics() -> dict[str, Any]:
    """Gather pipeline counts from the database for the status report."""
    return dict[str, Any](await VideoRepository().pipeline_snapshot())


async def check_audio_api_health() -> tuple[str, str]:
    """Probe the configured speech-to-text endpoint for liveness.

    Returns ``(status, detail)`` with status ``healthy`` / ``degraded`` / ``down``.
    Issues a tiny probe using the configured key; a 401/403 means reachable but
    bad key, a 2xx/4xx (non-timeout) means reachable, a connection error means
    down. Best-effort: never raises.
    """
    from atlas.config import get_settings

    settings = get_settings()
    if settings.GROK_API_KEY:
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        auth = f"Bearer {settings.GROK_API_KEY.get_secret_value()}"
        provider = "Groq Whisper"
    elif settings.MISTRAL_API_KEY:
        url = "https://api.mistral.ai/v1/audio/transcriptions"
        auth = f"{settings.MISTRAL_API_KEY.get_secret_value()}"
        provider = "Mistral Voxtral"
    else:
        return ("down", "No transcription API key configured")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # No audio body — we only want to confirm the endpoint is reachable
            # and the credential is accepted (a 4xx/422 is fine; a 401/403 is a
            # bad key; a connection error is a down endpoint).
            resp = await client.post(
                url,
                headers={"Authorization": auth, "Content-Type": "multipart/form-data"},
                files={"file": ("healthcheck.ogg", b"", "audio/ogg")},
            )
        if resp.status_code in (200, 201):
            return ("healthy", f"{provider} reachable")
        # Body may disambiguate a bad credential from a benign validation error.
        body = resp.text or ""
        if resp.status_code in (401, 403) or "incorrect api key" in body.lower():
            return ("degraded", f"{provider} reachable but key rejected (HTTP {resp.status_code})")
        if resp.status_code in (400, 413, 415, 422):
            # Wrong content-type / empty file etc. — endpoint is up.
            return ("healthy", f"{provider} reachable")
        return ("degraded", f"{provider} returned HTTP {resp.status_code}")
    except httpx.TimeoutException:
        return ("degraded", f"{provider} probe timed out")
    except httpx.HTTPError as e:
        return ("down", f"{provider} unreachable: {e}")
    except Exception as e:  # noqa: BLE001 - probe must never crash the cycle
        logger.warning(f"Audio API health probe failed: {e}")
        return ("down", f"Audio API probe error: {e}")


def _build_fields(services: dict[str, tuple[str, str]], metrics: dict[str, Any]) -> dict[str, str]:
    """Build the Discord embed fields from service states and pipeline metrics."""
    icons = {"healthy": "🟢", "warn": "🟡", "down": "🔴"}

    # Split the merged services dict into the executor (systemd unit) and the
    # Prefect deployments so each gets its own embed field.
    executor_line = "\n".join(
        f"{icons.get(health, '🔴')} `{unit}` — {label}"
        for unit, (label, health) in services.items()
        if unit in FLEET_UNITS
    )
    deployments_line = "\n".join(
        f"{icons.get(health, '🔴')} `{unit}` — {label}"
        for unit, (label, health) in services.items()
        if unit not in FLEET_UNITS
    )

    sc = metrics["status_counts"]
    pipeline_line = (
        f"PENDING: **{sc.get('PENDING', 0)}**\n"
        f"PROCESSING: **{sc.get('PROCESSING', 0)}**\n"
        f"PROCESSED: **{sc.get('PROCESSED', 0)}**\n"
        f"ARCHIVED: **{sc.get('ARCHIVED', 0)}**\n"
        f"FAILED: **{sc.get('FAILED', 0)}**"
    )

    content_line = (
        f"Videos: **{metrics['total']}**\n"
        f"Transcripts: **{metrics['transcripts']}**\n"
        f"With visuals: **{metrics['with_visuals']}**\n"
        f"Audios extracted: **{metrics['audios']}**\n"
        f"Ingested (1h): **{metrics['ingested_1h']}**"
    )

    return {
        "Executor": executor_line,
        "Deployments": deployments_line,
        "Pipeline": pipeline_line,
        "Content": content_line,
    }


@flow(name="heartbeat_cycle")
async def heartbeat_flow() -> dict[str, Any]:
    """Collect status, post to Discord, and return a summary dict."""
    from atlas.notifications import AlertChannel, AlertLevel, notifier

    services = await asyncio.to_thread(collect_service_status)
    try:
        fleet = await collect_fleet_status()
    except Exception as e:  # noqa: BLE001 - never let the fleet probe crash the cycle
        logger.warning(f"Fleet status collection failed: {e}")
        fleet = {nm: ("API unreachable", "down") for nm in FLEET_DEPLOYMENTS}
    # Merge executor liveness (systemd) with deployment health (Prefect API).
    services = {**services, **fleet}

    try:
        metrics = await collect_pipeline_metrics()
    except Exception as e:  # noqa: BLE001 - still report service status if DB is down
        logger.exception(f"Failed to collect pipeline metrics: {e}")
        metrics = {
            "total": "?",
            "status_counts": {},
            "transcripts": "?",
            "with_visuals": "?",
            "audios": "?",
            "ingested_1h": "?",
        }

    # Audio transcription API liveness (Grok STT / Mistral Voxtral).
    try:
        audio_status, audio_detail = await check_audio_api_health()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Audio API health check errored: {e}")
        audio_status, audio_detail = "down", f"error: {e}"

    # Agents currently paused by quota exhaustion (surfaced, not alerted).
    rate_limited = quota_exhausted_agents()

    fields = _build_fields(services, metrics)
    audio_icons = {"healthy": "🟢", "degraded": "🟡", "down": "🔴"}
    fields["Audio API"] = f"{audio_icons.get(audio_status, '🔴')} {audio_detail}"
    if rate_limited:
        fields["Quota"] = "⏳ Rate limited / quota exhausted: " + ", ".join(rate_limited)

    # Distinguish the executor worker (systemd) from the Prefect deployments.
    executor_down = [u for u in FLEET_UNITS if services.get(u, ("", "healthy"))[1] == "down"]
    fleet_down = [
        u for u, (_, health) in services.items()
        if u not in FLEET_UNITS and health == "down"
    ]
    degraded = [u for u, (_, health) in services.items() if health == "warn"]

    # A degraded audio API is surfaced but does not by itself flip the banner to
    # "Degraded" — only a truly down executor or deployment does. Quota-exhausted
    # agents are shown in the "Quota" field (and their alert is rate-limited).
    if executor_down or fleet_down:
        level = AlertLevel.WARNING
        status_word = "Degraded"
        description = f"⚠ Down: {', '.join(executor_down + fleet_down)}"
    elif rate_limited:
        level = AlertLevel.INFO
        status_word = "Rate Limited"
        description = (
            "Online — quota exhausted for: "
            + ", ".join(rate_limited)
            + " (transcription/API paused; alerts rate-limited)"
        )
    elif degraded or audio_status != "healthy":
        level = AlertLevel.INFO
        status_word = "Online"
        extra = []
        if degraded:
            extra.append(f"{len(degraded)} service(s) degraded: {', '.join(degraded)}")
        if audio_status != "healthy":
            extra.append(f"audio API {audio_status}")
        description = "Online — " + "; ".join(extra)
    else:
        level = AlertLevel.SUCCESS
        status_word = "Online"
        description = "All fleet services are online. ✅"

    await notifier.send(
        title=f"🛰 Pleiades Fleet Status: {status_word}",
        description=description,
        channel=AlertChannel.OPS,
        level=level,
        fields=fields,
    )

    summary = {
        "healthy": not executor_down and not fleet_down and not degraded,
        "down": executor_down + fleet_down,
        "degraded": degraded,
        "metrics": metrics,
    }
    logger.info(
        f"Heartbeat sent: {status_word} "
        f"(executor_down={executor_down}, fleet_down={fleet_down}, degraded={degraded})"
    )
    return summary


class HeartbeatAgent:
    """Heartbeat Agent: posts periodic fleet online-status to Discord."""

    name = "heartbeat"

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        # No arguments — a single status snapshot per invocation.
        return None

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        return await heartbeat_flow()


def main() -> None:
    try:
        asyncio.run(HeartbeatAgent().run())
    except KeyboardInterrupt:
        logger.info("Heartbeat stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Heartbeat failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
