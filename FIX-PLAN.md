# Action Plans — Fix concurrency / FAILED backlog + bring micro (control plane) back up

Environment facts (verified this session):
- OCI CLI 3.89.1 configured at `~/.oci/config` (region `eu-frankfurt-1`, tenancy
  `ocid1.tenancy.oc1..aaaaaaaawu7psjcwcpnycz5ellw3obofafrdcaph7ythryiqzpckw2f4qhvq`).
- Instances in root tenancy compartment. The control-plane micro is:
  `ocid1.instance.oc1.eu-frankfurt-1.antheljtf43hpxic6ee7l5vor5nis7eaf5ycfj7f4z4agxg6v6yzv2z4he2q`
  display-name `e2-micro-server`, state `STOPPED`, AD-3. (Stateless OCID → boots back to 10.0.0.22.)
- Prefect CLI 3.7.7 in `/home/ubuntu/code/pleiades/.venv`. Micro API `http://10.0.0.22:4200/api`
  currently unreachable (micro off). Worker on this VPS runs with `PREFECT_API_URL=…:4200/api`.
- Executor-side backstop already live: `/etc/systemd/system/prefect-worker.service` has
  `CPUQuota=180%`, `TasksMax=120`.

The two plans below are ORDERED: Plan B (start micro) is a prerequisite for Plan A (the actual
concurrency fix), because the work-pool concurrency limit and deployments live in the micro's
Prefect API (SQLite), not on this VPS.

================================================================================
PLAN B — Power the micro back on via OCI CLI  (prerequisite)
================================================================================
Per OCI skill: any command that mutates OCI resources needs explicit user approval. Validate
state first (read-only), then start.

B0. (optional, verify OCID) 
    export TENANCY=ocid1.tenancy.oc1..aaaaaaaawu7psjcwcpnycz5ellw3obofafrdcaph7ythryiqzpckw2f4qhvq
    oci compute instance list -c $TENANCY --all
    # expect e2-micro-server = STOPPED (confirm OCID above)

B1. START the micro (mutating — requires your go-ahead):
    oci compute instance action \
      --instance-id ocid1.instance.oc1.eu-frankfurt-1.antheljtf43hpxic6ee7l5vor5nis7eaf5ycfj7f4z4agxg6v6yzv2z4he2q \
      --action START
    # returns the instance; lifecycle-state goes STOPPED → STARTING → RUNNING (~30-60s for an e2-micro)

B2. Poll until RUNNING:
    oci compute instance get \
      --instance-id ocid1.instance.oc1.eu-frankfurt-1.antheljtf43hpxic6ee7l5vor5nis7eaf5ycfj7f4z4agxg6v6yzv2z4he2q \
      --query 'data."lifecycle-state"'

B3. Confirm the private IP is back (should be 10.0.0.22 — static):
    oci compute instance list-vnics \
      --instance-id ocid1.instance.oc1.eu-frankfurt-1.antheljtf43hpxic6ee7l5vor5nis7eaf5ycfj7f4z4agxg6v6yzv2z4he2q \
      --query 'data[].{"private-ip":"private-ip","public-ip":"public-ip"}'

B4. Wait for the Prefect server to come up (it's a systemd unit `prefect-server.service` on the micro):
    # from this VPS:
    for i in $(seq 1 30); do
      curl -s http://10.0.0.22:4200/api/health && break
      sleep 5
    done
    # expected JSON: {"status":"ok"} (or similar). Server auto-starts via systemd on boot.

B5. Confirm the worker on this VPS reconnects (it polls the API; no restart strictly required, but
    safest to restart so it re-registers cleanly):
    sudo systemctl status prefect-worker

================================================================================
PLAN A — Fix the concurrency overload + drain the FAILED backlog  (Prefect)
================================================================================
Guided by the prefect-flow-builder skill: production-ready config + validation against the API.
Root cause: `max_active_runs: 1` in prefect.yaml is PER-DEPLOYMENT; the `default` work pool had NO
global `concurrency_limit` (unlimited), so up to 9 flow-runs ran at once → CPU death spiral on the
2-core VPS. PRIMARY fix = global work-pool concurrency cap. (The cgroup backstop is defense-in-depth
and already live.)

Run all of A* on this VPS with the venv active AND the micro API reachable (i.e. after Plan B):

  source /home/ubuntu/code/pleiades/.venv/bin/activate
  export PREFECT_API_URL=http://10.0.0.22:4200/api

A1. PRIMARY FIX — set a GLOBAL work-pool concurrency limit = 2 (caps total concurrent flow-runs
    across all deployments to 2 on the 2-core box):
    prefect work-pool set-concurrency-limit default 2
    # verify:
    prefect work-pool inspect default          # look for "concurrency_limit": 2

A2. GUARANTEE per-deployment cap — re-apply all 9 deployments from prefect.yaml so
    `max_active_runs: 1` + schedules + env are definitely enforced (6-concurrent-singer evidence
    implies it wasn't taking effect before):
    cd /home/ubuntu/code/pleiades
    prefect deploy --all
    # (if --all prompts, run per name: prefect deploy -n streamer -n singer ... -n janitor)
    # verify:
    prefect deployment ls                       # expect 9 deployments, all "Ready"

A3. Restart the executor worker so it picks up the new pool limit immediately (cgroup cap stays live):
    sudo systemctl restart prefect-worker
    sudo systemctl status prefect-worker        # active (running)

A4. DRAIN the backlog — re-run the surgical FAILED reset. Current count = 48
    (30 audio_stuck + 12 muralist-claimed + 6 visuals_stuck). With concurrency now bounded to 2,
    these reprocess without saturating the box:
    python - <<'PY'
    import asyncio, os
    from pathlib import Path
    for line in Path(".env").read_text().splitlines():
        line=line.strip()
        if line and not line.startswith("#") and "=" in line:
            k,_,v=line.partition("="); k=k.strip(); v=v.strip().strip('"').strip("'")
            if " #" in v: v=v.split(" #",1)[0].strip()
            if k and k not in os.environ: os.environ[k]=v
    from atlas.repositories import VideoRepository
    async def main():
        repo=VideoRepository()
        n=await repo._execute("UPDATE videos SET status='PENDING', last_updated_at=now() WHERE status='FAILED'")
        print("reset FAILED -> PENDING:", n)
    asyncio.run(main())
    PY

A5. VALIDATE (let a few cycles run, then check):
    - CPU: `uptime` / `htop` — load should stay ≪ 2×cores (was 26-27).
    - FAILED should NOT climb back to 48; watch it drain:
      psql -d pleiades -c "SELECT status, count(*) FROM videos GROUP BY status ORDER BY 2 DESC;"
    - Prefect side: `prefect work-pool inspect default` shows concurrency_limit=2;
      `prefect deployment ls` shows runs scheduled; no more than 2 flow-runs executing at once
      (`prefect flow-run ls --state RUNNING` should be ≤ 2).

A6. (separate, non-blocking) The 12 muralist-stranded "all-done" videos: once audio/visuals/transcript
    are DONE, they are functionally complete. Decide separately whether to treat the muralist clip
    stage as non-blocking for `PROCESSED`, or run muralist manually. Out of scope for the concurrency fix.

NOTES / SAFETY:
- Do NOT use `tools/recover_failed_runs.py --apply`; its frame-purge would force ~115 needless frame
  regenerations. The inline SQL in A4 is the surgical reset used before (proven safe: 145→0).
- The cgroup backstop (CPUQuota=180%/TasksMax=120) means even if the pool cap is mis-set, the box
  cannot be fully saturated. Keep it.
- Nothing here has been executed yet — both plans require your explicit approval to run.
