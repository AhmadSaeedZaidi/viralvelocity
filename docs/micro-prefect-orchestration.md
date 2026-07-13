# Micro Instance — Prefect Orchestration Runbook

Operational reference for the Pleiades Prefect control plane (the Oracle `e2-micro-server`)
and the executor VPS that runs the worker. Written 2026-07-13 after a full incident + reconfiguration.
Keep this in sync if the architecture changes.

---

## 1. Topology (don't forget this)

| Role | Host | What runs |
|------|------|-----------|
| **Control plane (micro)** | `e2-micro-server`, private `10.0.0.22`, public `141.147.60.138`, AD-3, eu-frankfurt-1 | Prefect **server/API** on `:4200` (SQLite at `/opt/prefect/prefect.db`) |
| **Executor (this VPS)** | `10.0.0.6` (2-core) | one `prefect-worker` service pulling the `default` pool; runs the actual flows (ffmpeg/yt-dlp) |

- The flow code (`maia/`, `atlas/`) lives on **this VPS** and is executed here. The micro only stores
  *orchestration* state (deployments, schedules, flow-run state) — **not** video data.
- The live **video** DB is `pleiades` @ `127.0.0.1:5432` on this VPS (separate from the micro's SQLite).

### Key identifiers
- Micro instance OCID: `ocid1.instance.oc1.eu-frankfurt-1.antheljtf43hpxic6ee7l5vor5nis7eaf5ycfj7f4z4agxg6v6yzv2z4he2q`
- Tenancy: `ocid1.tenancy.oc1..aaaaaaaawu7psjcwcpnycz5ellw3obofafrdcaph7ythryiqzpckw2f4qhvq`
- OCI CLI config: `~/.oci/config` (region `eu-frankfurt-1`). SSH: `~/.ssh/pleiades-mini-key` → `ubuntu@10.0.0.22`.

---

## 2. Powering the micro on/off (OCI CLI)

The micro is just an Oracle Compute instance. All credentials for `oci` are already on this VPS.

```bash
export TENANCY=ocid1.tenancy.oc1..aaaaaaaawu7psjcwcpnycz5ellw3obofafrdcaph7ythryiqzpckw2f4qhvq
export MICRO=ocid1.instance.oc1.eu-frankfurt-1.antheljtf43hpxic6ee7l5vor5nis7eaf5ycfj7f4z4agxg6v6yzv2z4he2q

# list instances (read-only)
oci compute instance list -c $TENANCY --all

# START (mutating — needs approval)
oci compute instance action --instance-id $MICRO --action START
# STOP
oci compute instance action --instance-id $MICRO --action STOP

# wait for RUNNING, confirm IP back to 10.0.0.22, then API health
for i in $(seq 1 30); do curl -s http://10.0.0.22:4200/api/health && break; sleep 5; done
```

The micro's OCID is static, so it always boots back to `10.0.0.22`. The Prefect server auto-starts
via `prefect-server.service`.

---

## 3. Concurrency / parallelism architecture (CRITICAL — read before changing)

After the 2026-07-13 incident the orchestration is configured as:

- **Work pool `default`** — global `concurrency_limit = 3` (CPU-safe ceiling for the 2-core box).
- **One work queue per deployment**, each `concurrency_limit = 1`:
  `streamer, singer, painter, scribe, hunter, tracker, archeologist, janitor` (priority 2) and
  `heartbeat` (priority 1).
- All 9 queues live in the `default` pool. Effect: **each agent runs ≤1 instance, but up to 3
  *different* agents run in parallel.** The heartbeat (priority 1) is never starved.

This is the idiomatic Prefect-3 equivalent of the old "9 independent systemd units" — multiple stages
progress concurrently without any single agent flooding CPU.

### ⚠️ GOTCHA: `max_active_runs` does NOT work in Prefect 3.7.7
In this build:
- `Deployment.concurrency_options` has **no `max_active_runs` field** (only `collision_strategy` + `grace_period_seconds`).
- `Deployment.concurrency_limit` (int) **does not persist** (read-back is always `None`).
- So `prefect.yaml` keys `max_active_runs: 1` and `concurrency_limit: {limit: 1, …}` are **silently ignored**.

The **only** mechanism that enforces per-deployment isolation is the **work-queue `concurrency_limit`**.
Work queues are DB objects (they survive `prefect deploy`); deployments just reference them via
`work_queue_name`. If you ever redeploy from `prefect.yaml`, make sure each deployment's `work_queue_name`
is set to its own queue name, otherwise it falls back to the unlimited `default` queue and isolation breaks.

---

## 4. Config files

- **Micro** `/etc/systemd/system/prefect-server.service` — key line:
  `Environment=PREFECT_SERVER_DATABASE_CONNECTION_URL=sqlite+aiosqlite:////opt/prefect/prefect.db?timeout=30`
  (the `?timeout=30` raises SQLite `busy_timeout` 5s→30s so writes retry instead of 503-ing).
- **This VPS** `/etc/systemd/system/prefect-worker.service` — `PREFECT_API_URL=http://10.0.0.22:4200/api`,
  plus cgroup backstop `CPUQuota=180%` / `TasksMax=120` (so the box can never be fully saturated even
  if Prefect misconfigures).
- **Repo** `prefect.yaml` — 9 deployments; each `work_pool.work_queue_name` points to its own queue.

---

## 5. Everyday operations

Run on **this VPS** with the venv + API URL set:
```bash
source /home/ubuntu/code/pleiades/.venv/bin/activate
export PREFECT_API_URL=http://10.0.0.22:4200/api
cd /home/ubuntu/code/pleiades
```

| Task | Command |
|------|---------|
| Worker status / restart | `systemctl status prefect-worker` / `sudo systemctl restart prefect-worker` |
| List deployments | `prefect deployment ls` |
| Pool + limit | `prefect work-pool inspect default` |
| Set global pool limit | `prefect work-pool set-concurrency-limit default 3` |
| Re-apply all deployments | `prefect deploy --all` |
| Re-apply one | `prefect deploy -n <name>` |
| Trigger heartbeat now | `prefect deployment run heartbeat_cycle/heartbeat` |
| Flow-run states | `prefect flow-run ls --state RUNNING` |

### Work-queue management (rarely needed)
```python
import asyncio
from prefect.client.orchestration import get_client
async def main():
    c = get_client()
    qs = await c.read_work_queues(work_pool_name="default")
    for q in qs:
        print(q.name, "priority", q.priority, "limit", q.concurrency_limit)
asyncio.run(main())
```
Per-deployment `concurrency_limit` and `priority` are set via
`client.update_work_queue(queue_id, concurrency_limit=1, priority=…)` / `client.create_work_queue(...)`.
Assign a deployment to a queue with `client.update_deployment(dep_id, deployment=DeploymentUpdate(work_queue_name=…))`.

---

## 6. Troubleshooting playbook

### A. `503 Service Unavailable` from the micro / `database is locked`
The micro is a 1/8-OCPU box on SQLite. Under write contention (e.g. right after a restart, or a
control-plane outage) the server 503s with `sqlite3.OperationalError: database is locked`.
- Fix already applied: server connection URL has `?timeout=30` (retries instead of failing).
- If it recurs badly, **restart the micro's Prefect server**:
  `ssh -i ~/.ssh/pleiades-mini-key ubuntu@10.0.0.22 'sudo systemctl restart prefect-server'`.
- Robust long-term fix: **migrate the orchestration DB to Postgres** (kills SQLite lock noise).

### B. Worker won't start any runs / pool shows "full" (`active_slots` high, 0 RUNNING)
After a control-plane outage, **orphaned flow runs** from the pre-outage worker hold work-pool slots
forever (their worker is gone, so Late-detection can't finalize them) → the pool reads as full and the
new worker starves. The slot-holders are typically the few `PENDING` runs with `start_time=None`, plus
any `RUNNING`/`CANCELLED`/`CRASHED` orphans.
- Clear them (server-side, fast — Prefect API):
  ```python
  import asyncio
  from prefect.client.orchestration import get_client
  from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterState, FlowRunFilterStateType
  async def main():
      c = get_client()
      f = FlowRunFilter(state=FlowRunFilterState(type=FlowRunFilterStateType(
          any_=["RUNNING","CANCELLING","CRASHED","PAUSED","PENDING"])))
      for r in await c.read_flow_runs(flow_run_filter=f, limit=500):
          try: await c.delete_flow_run(r.id)
          except Exception as e: print("err", r.name, e)
  asyncio.run(main())
  ```
- After cleanup, `prefect work-pool inspect default` should show `active_slots` drop to 0 and the worker
  starts picking up work. (If deleting 200+ runs via API is too slow / causes lock storms, stop the
  micro server and delete the orphan rows directly from `/opt/prefect/prefect.db` via `sqlite3`/python
  on the micro, then restart the server.)

### C. Heartbeat stopped posting to Discord
- Check it's scheduled + not starved: `prefect deployment ls`, `prefect flow-run ls --state RUNNING`.
- If starved, confirm the `heartbeat` queue exists with priority 1 (see §3) and that the deployment's
  `work_queue_name` is `heartbeat`. Force one: `prefect deployment run heartbeat_cycle/heartbeat`.
- If it runs but fails, check the run's state message (Discord webhook/creds).

### D. CPU saturation (load >> cores)
- Confirm cgroup backstop: `systemctl cat prefect-worker | grep -E 'CPUQuota|TasksMax'`.
- Confirm pool `concurrency_limit=3` and per-deployment queues `limit=1` are intact (§3). A redeploy
  that reverted `work_queue_name` to `default` would let one deployment grab multiple slots.

---

## 7. Known open items
- **Muralist** is manual-only (no deployment); 12 "all-done" videos (audio/transcript/visuals DONE,
  `has_video=FALSE`, `clip_phase=PENDING`) are claimed by muralist and were marked FAILED by a manual
  run. Not blocking; decide separately whether to make the clip stage non-blocking for `PROCESSED`.
- **Orchestration DB on SQLite** → residual lock-noise under load. Postgres migration is the durable fix.
- All source changes from the 2026-07-13 work are **local/uncommitted** on `hy3-work` (push on approval).
