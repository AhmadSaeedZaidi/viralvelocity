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

After the 2026-07-13 incident the orchestration was configured with a global
`concurrency_limit = 5` and strict queue priorities (heartbeat=1 … streamer=9).
**This caused priority starvation:** the five cheap-but-frequent agents
(heartbeat/janitor/archeologist/tracker/hunter) filled the five available slots,
and the streamer (priority 9 — the *input* to the entire pipeline) never got
scheduled. Singer and painter (priority 7-8) were also starved. Result: no new
raw videos were fetched → audio/visual extraction sat idle despite 23,741
PENDING raw videos.  This was fixed on 2026-07-14.

### Why (thread exhaustion)
The 2026-07-13 incident was about **scheduling** (priority starvation). The
2026-08-03 change is about **execution cost**: each flow run spawned a full
`python -m prefect.engine` subprocess (~17 threads, 170-400 MB RSS); 9 slots ×
17 threads ≈ 153 routinely blew the `TasksMax` ceiling → `can't start new
thread`. See §8 — an in-process orchestrator now drives the same flows on one
asyncio loop with no subprocess per cycle.

### Why

Current configuration:

  - **Work pool `default`** — global `concurrency_limit = 9` (one slot per queue).
    With per-queue `limit=1` the worst case is 9 concurrent runs, safe under the
    worker's `CPUQuota=180%` cgroup backstop (most agents are cheap DB queries;
    the heavy ffmpeg agents are limited to 1 each anyway).
- **One work queue per deployment**, each `concurrency_limit = 1`:
  `streamer, singer, painter, scribe, hunter, tracker, archeologist, janitor`
  (priority 2) and `heartbeat` (priority 1).
  - All 9 queues live in the `default` pool. Effect: **each agent runs ≤1
    instance, all 9 can run concurrently.** Priorities exist only for UI ordering;
    with `concurrency_limit=9` they do not gate scheduling — there is always a
    slot available for every queue that has work.

This is the idiomatic Prefect-3 equivalent of the old "9 independent systemd
units" — multiple stages progress concurrently without any single agent flooding
CPU.

### ⚠️ GOTCHA: `max_active_runs` does NOT work in Prefect 3.7.7
In this build:
- `Deployment.concurrency_options` has **no `max_active_runs` field** (only `collision_strategy` + `grace_period_seconds`).
- `Deployment.concurrency_limit` (int) **does not persist** (read-back is always `None`).
- So `prefect.yaml` keys `max_active_runs: 1` and `concurrency_limit: {limit: 1, …}` are **silently ignored**.

The **only** mechanism that enforces per-deployment isolation is the **work-queue `concurrency_limit`**.
Work queues are DB objects (they survive `prefect deploy`); deployments just reference them via
`work_queue_name`. If you ever redeploy from `prefect.yaml`, make sure each deployment's `work_queue_name`
is set to its own queue name, otherwise it falls back to the unlimited `default` queue and isolation breaks.

### ⚠️ GOTCHA: micro Prefect API caps `read_flow_runs` at `limit=200`
Any `client.read_flow_runs(limit=N)` / `read_*_runs` with `N > 200` returns **`422 Unprocessable Entity`**
from the micro server. This bit the heartbeat: `collect_fleet_status()` used `limit=500`, got a 422, and
the `except Exception` masked it as "API unreachable" for *every* deployment (false total outage).
**Fix:** page (≤200) or query **per-deployment** (`read_flow_runs(flow_run_filter=deployment_id, limit=1)`).
Never assume a large `limit` is accepted.

---

## 4. Config files

- **Micro** `/etc/systemd/system/prefect-server.service` — key line:
  `Environment=PREFECT_SERVER_DATABASE_CONNECTION_URL=sqlite+aiosqlite:////opt/prefect/prefect.db?timeout=30`
  (the `?timeout=30` raises SQLite `busy_timeout` 5s→30s so writes retry instead of 503-ing).
- **This VPS** `/etc/systemd/system/prefect-orchestrator.service` — `PREFECT_API_URL=http://10.0.0.22:4200/api`,
  cgroup `CPUQuota=180%` / `TasksMax=256` / `MemoryMax=4G`; runs the fleet in-process (see §8).
  The old `prefect-worker.service` is disabled (rollback path in §8).
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
| Orchestrator status / restart | `systemctl status prefect-orchestrator` / `sudo systemctl restart prefect-orchestrator` |
| Watch fleet cycles | `sudo journalctl -u prefect-orchestrator -f` |
| (Rollback) legacy worker | `prefect-worker` is disabled; see §8 rollback |
| List deployments | `prefect deployment ls` |
| Pool + limit | `prefect work-pool inspect default` |
| Set global pool limit | `prefect work-pool set-concurrency-limit default 9` |
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

For a **reproducible** (re)creation of the whole queue topology after a micro
rebuild or work-pool recreation, run the checked-in script (reads
`PREFECT_API_URL` from the environment; contains **no secrets**):

```bash
export PREFECT_API_URL=http://10.0.0.22:4200/api
python tools/setup_orchestration.py
```

It sets the pool global `concurrency_limit=9` and creates/updates all 9 queues
with `limit=1`, idempotently.

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
- Confirm cgroup backstop: `systemctl cat prefect-orchestrator | grep -E 'CPUQuota|TasksMax'`.
- Confirm pool `concurrency_limit=9` and per-deployment queues `limit=1` are intact (§3). A redeploy
  that reverted `work_queue_name` to `default` would let one deployment grab multiple slots.

### E. Priority starvation (all slots full, but some queues never run)
If one or more queues never get scheduled (e.g. the streamer's raw-fetch stalls
while heartbeat/janitor/tracker are always active), check that the pool global
`concurrency_limit` is not below the number of queues that need slots.  With 9
queues each `limit=1`, the pool limit must be ≥9 to give every queue a fair
chance.  History: `concurrency_limit=5` caused the 2026-07-13 pipeline stall
(23,741 videos PENDING because the streamer was starved by priorities 1-5).
Fix: `prefect work-pool set-concurrency-limit default 9`.

---

## 7. Known open items
- **Painter/Singer are INPUT-STARVED, not scheduling-starved (diagnosed 2026-07-14).** The
  `concurrency_limit=9` fix is correct and in place. The real cause is upstream: the **streamer**
  is ~80% failing YouTube `HTTP 403 Forbidden` on the flagged VPS egress IP (logs show
  `yt-dlp rate-limited ... HTTP Error 403: Forbidden` on 4–5 of every 5 claimed videos; only
  ~1 succeeds). Net throughput ≈40 fetched/day vs ~3600/day theoretical. Painter/singer consume
  `fetched` rows; all 1,255 ever-fetched videos already have visuals+audio, so
  `claim_painter_batch`/`claim_singer_batch` return **0** — they are idle, not broken. Scribe
  bypasses this entirely (free YouTube captions, no fetch) and raced to 14,133 transcripts.
  **Fix is on the streamer/YouTube-access layer, not painter/singer:**
    - renew `YOUTUBE_COOKIES_PATH` + PoToken; try yt-dlp `player_client` variants
      (`tv`/`tv_embedded`/`web_silk`/`android`) to dodge the 403;
    - route the egress through a clean IP/proxy (current one is flagged) — note both the
      executor and the micro are OCI **datacenter** IPs, so YouTube will likely still 403 a
      proxy through the micro; a **residential** proxy is the durable fix if it's IP-class;
    - **backoff/cooldown IMPLEMENTED (2026-07-14):** `StreamerAgent.run()` now checks an
      on-disk rate-limit cooldown (`atlas.state`): after a 403 storm it *skips* cycles and
      backs off exponentially (300s → 1h cap) instead of re-claiming every 120s and hammering
      the flagged IP (which worsened the block). A clean cycle resets the cooldown. Fresh
      cookies were also deployed (`pleiades/www.youtube.cookies.txt`). Monitoring 24h to see
      whether cookie freshness + backoff restores fetch throughput.
  - **Secondary defect — FIXED (2026-07-14).** A successful fetch failed to persist:
    `Batched videos' raw+meta store failed (1 items): RetryError[... raised AttributeError]`
    (vault `store` path), so even good fetches were lost. **Root cause:** `commit_artifacts`
    (added in P3) received the `get_vault` *factory function* instead of the live instance
    (callers passed `vault=get_vault`, and `commit_artifacts` only resolved `vault` when
    `store is None`; since the agents inject `store=vault_op_with_retry`, `vault` stayed the
    function and the lambda `vault.store_batch(...)` raised `AttributeError`). Every
    streamer/singer/muralist batch store failed → no `raw_uri`/`audio`/`clip` ever persisted after
    the P3 deploy, compounding the painter/singer starvation. **Fix:** `commit_artifacts` now
    resolves a callable `vault` to its instance, and the 3 callers pass `get_vault()`; a
    regression test in `maia/tests/test_storage.py` actually executes the store lambda (the
    `AsyncMock` patch had hidden the bug).
- **`status` column is misleading (data hygiene).** 12,901 of 12,922 `PROCESSING` rows are
  *not fetched* — they are scribe'd (caption) videos whose `status` was set to `PROCESSING` by the
  claim but never reset, because `mark_transcript_safe`/`mark_audio_safe`/`mark_visuals_safe` only
  latch `PROCESSED` when the *other* artifacts are also present and otherwise leave `status` as-is.
  So every touched-but-incomplete video reads `PROCESSING` forever. Not a starvation cause (claim
  queries correctly accept `PENDING`/`PROCESSING`), but it makes the heartbeat "PROCESSING: 12,925"
  figure terrifying and pollutes ops. Consider resetting `status` to `PENDING` on a non-latching
  step, or keying the "claimed" concept off a lease/`claimed_at` timestamp instead of `status`.
- **Muralist** is manual-only (no deployment); ~12 "all-done" videos (audio/transcript/visuals DONE,
  `has_video=FALSE`, `clip_phase=PENDING`) are claimed by muralist and were marked FAILED by a manual
  run. Not blocking; decide separately whether to make the clip stage non-blocking for `PROCESSED`.
- **Orchestration DB on SQLite** → residual lock-noise under load. Postgres migration is the durable fix.
- **Janitor archival was shrinking heartbeat values — FIXED (2026-08-03).** The janitor's
  hand-off phase (atlas `VideoJanitorMixin`) unconditionally ran `UPDATE videos SET has_transcript/
  has_audio/has_visuals = FALSE` + `DELETE FROM transcripts` + `DELETE FROM video_stats_log` for every
  swept `PROCESSED` video — **with no check that the transcript content had ever reached the vault**.
  Two consequences:
    - **Metrics regression:** the heartbeat's `pipeline_snapshot` counts the hot tier
      (`transcripts`, `with_visuals`, `audios`, `stats_log_size`). Every cycle that archival ran,
      these numbers dropped → the fleet status looked like it was losing data.
    - **Potential data loss:** `vault_flush_task` only flushes transcripts where
      `vault_write_pending=TRUE OR vault_uri IS NULL`. If the janitor's sweep+hand-off ran *before*
      the flush for a video, the transcript row was DELETEd with its content never written to the
      vault — irreversibly. Confirmed live: `SuPwt5XJuRU` had `has_transcript=True` at archive yet
      `fetch_transcript()` is empty.
   **Fix (both ships):** the sweep/count queries are now gated by `_VAULT_SAFE_CLAUSE` — a video is
   only archivable when `vault_write_pending IS NOT TRUE` and no transcript row has
   `vault_uri IS NULL`. Defense-in-depth: `archive_video_batch` also refuses to purge a video whose
   transcript still lacks a vault `vault_uri`. `archive_cold_stats_task` already deleted cold-stats
   only after a successful vault append, so it was left unchanged. Regression tests:
   `atlas/tests/test_janitor_vault_gate.py`. Verified live: the newest archived video's transcript
   is present in the vault. Note: this guards *future* archival; it does not retroactively rescue
   already-deleted unflushed transcripts.
- Source lives on the `hy3-work` branch (committed + pushed); see `docs/deploy.md` for the reproduce
  script (`tools/setup_orchestration.py`) and `docs/CHALLENGES.md` for the engineering log.

---

## 8. In-process orchestrator (2026-08-03) — replaces the subprocess worker

### Why
The scheduler + `prefect worker start --type process` model spawned a **full
`python -m prefect.engine` subprocess per flow run** (~17 threads,
170-400 MB RSS each). With 9 flow slots that regularly exceeded the worker's
systemd `TasksMax=120` ceiling → `RuntimeError: can't start new thread`, plus
~250 MB + ~17 threads of pure interpreter churn on every 60-900s cycle.

### What changed
- **New** `maia/src/maia/orchestrator.py` — a single long-running asyncio
  process that calls each deployment's `@flow` entrypoint **in-process**.
  Because `PREFECT_API_URL` is set, Prefect still creates a real flow run in
  the micro control plane (so `get_run_logger()`, retries, and run telemetry
  all keep working) — there is just **no subprocess spawn** per cycle.
- **New** systemd unit `/etc/systemd/system/prefect-orchestrator.service`
  (`TasksMax=256`, `MemoryMax=4G`, `CPUQuota=180%`), enabled + active.
- The old `prefect-worker.service` was **stopped and disabled**. Schedules for
  all 9 deployments were **paused** (`prefect deployment schedule pause ...`)
  so the scheduler no longer creates runs — only the orchestrator's in-process
  loops drive cycles now. Cycle cadence/params mirror `prefect.yaml`.

### Manage
```bash
sudo systemctl status/restart prefect-orchestrator   # must be active (running)
sudo journalctl -u prefect-orchestrator -f            # watch cycles
```
Intervals: tracker 60s, streamer/painter/scribe 120s, singer/hunter 300s,
archeologist 600s, heartbeat/janitor 900s. Agent `kwargs` match `prefect.yaml`.

### ROLLBACK (restore the old subprocess worker)
1. Delete/disable the orchestrator: `sudo systemctl disable --now prefect-orchestrator`.
   (Optional: `sudo rm /etc/systemd/system/prefect-orchestrator.service && sudo systemctl daemon-reload`.)
2. Re-enable the worker: `sudo systemctl enable --now prefect-worker` (it still
   exists; only stopped/disabled).
3. Resume the 9 deployment schedules. Each pair is `<flow>/<deployment>`:
   ```bash
   export PREFECT_API_URL=http://10.0.0.22:4200/api
   for d in run_streamer_cycle/streamer run_painter_cycle/painter run_scribe_cycle/scribe \
            run_hunter_cycle/hunter run_tracker_cycle/tracker run_archeology_campaign/archeologist \
            janitor_cycle/janitor heartbeat_cycle/heartbeat run_singer_cycle/singer; do
     prefect deployment schedule resume "$d"
   done
   ```
   If `resume` needs a schedule ID, read it from `prefect deployment schedule ls "$d"`.
4. Verify the worker starts runs again (`prefect flow-run ls --state RUNNING`).

### Monitoring note
`pids.current` in the orchestrator cgroup stays ~26-40 (was 120+ thrashing);
the box idles instead of re-spawning interpreters. hunter/archeologist still
fail on YouTube API **daily quota** (pre-existing, unrelated) — their loops
swallow the error and retry on the next interval.
