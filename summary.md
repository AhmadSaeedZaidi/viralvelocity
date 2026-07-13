# Pleiades — Work Summary (compacted context)

Branch: `hy3-work` (pushed to `origin` for early commits; many later changes are LOCAL/UNCOMMITTED).
Live DB: `pleiades` @ `127.0.0.1:5432/pleiades` (this VPS). Prefect control plane: micro `10.0.0.22:4200`
(SQLite). This VPS = executor (runs `prefect-worker`); micro = control plane (API + orchestration DB).
**Micro is ON again** (brought back up 2026-07-13 via OCI CLI; `e2-micro-server` RUNNING at 10.0.0.22).

## CURRENT PIPELINE STATUS (2026-07-13, after fix)
- **Parallelism / concurrency architecture (FIXED 2026-07-13, after "laggy" complaint)**:
  - `default` work pool `concurrency_limit=3` = CPU-safe global cap (was 2 → starved everything;
    was unlimited → 9 concurrent → CPU death spiral load 26-27).
  - **Per-deployment isolation via work queues** (the working Prefect-3 mechanism): one work queue
    per deployment (`streamer/singer/painter/scribe/hunter/tracker/archeologist/janitor/heartbeat`),
    each `concurrency_limit=1`, all in the `default` pool. So each agent runs ≤1 instance, but up to
    3 *different* agents run in parallel (responsive, like the old 9-systemd-units feel).
  - **Heartbeat queue priority 1** (all others priority 2), so the heartbeat never starves.
  - **GOTCHA (important):** in this Prefect 3.7.7 build, per-deployment `max_active_runs` is NOT
    supported — `ConcurrencyOptions` has no `max_active_runs` field and `Deployment.concurrency_limit`
    (int) does not persist. `prefect.yaml` `max_active_runs: 1` and `concurrency_limit: {limit:1,…}`
    are silently ignored. The real per-deployment cap comes ONLY from the work-queue `concurrency_limit`.
    Work queues are DB objects (persist across `prefect deploy`); deployments just reference them via
    `work_queue_name` (set per-deployment in prefect.yaml so redeploys don't revert to `default`).
- **Micro SQLite lock storm FIXED**: server connection URL patched to
  `sqlite+aiosqlite:////opt/prefect/prefect.db?timeout=30` in
  `/etc/systemd/system/prefect-server.service` on the micro (raised busy_timeout from 5s→30s so
  writes retry instead of 503 "database is locked"). This was the blocker that stopped `prefect deploy`.
- **Orphan-slot cleanup** (learned the hard way): after a control-plane outage, orphaned flow runs
  from the pre-outage worker hold work-pool slots forever (their worker is gone, so Late-detection
  can't finalize them) → pool reads as "full" and the new worker won't start anything. Fix: delete
  the orphaned runs via API/DB (the slot-holders were the few `PENDING` runs with `start_time=None`;
  `RUNNING`/`CRASHED` orphans also hold slots). After cleanup `active_slots` dropped to 0.
- **FAILED = 0** in the videos DB. The "48 stuck" videos were never `status='FAILED'` — they're in
  `PENDING` (13,911) / `PROCESSING` (6,610), which the bounded worker reclaims via staleness.
  The dead-end `FAILED` problem is resolved; backlog drains at concurrency 2.
- **Muralist 12 "all-done"** videos still separate (in PROCESSING, has_video=FALSE, clip_phase=PENDING);
  muralist is manual-only. Not blocking.
- Residual: ~28 "database is locked" log lines/min on the micro (retried, non-fatal). SQLite on a
  1/8-OCPU e2-micro is the bottleneck; robust fix = migrate orchestration DB to Postgres, or raise
  `PREFECT_API_SERVICES_*_LOOP_SECONDS`. Functional as-is.

---

## 1. Architecture (how Prefect works here)

- **Control plane (micro `10.0.0.22`)**: Prefect server/API on `:4200`, SQLite backend. Stores
  *orchestration* state only (deployments, schedules, flow-run state). Does NOT store video data.
- **Execution plane (this 2-core VPS)**: one `prefect-worker` daemon (`/etc/systemd/system/
  prefect-worker.service` → `prefect worker start --pool default --type process`). Polls the API
  and executes each queued run as a **separate subprocess** (`python -m prefect.engine`).
- **9 deployments** (in `prefect.yaml`): `streamer, singer, painter, scribe, hunter, tracker,
  archeologist, heartbeat, janitor`. Each = a `@flow` + schedule (interval) + `max_active_runs: 1`
  + `work_pool: default`. **Muralist is intentionally NOT a deployment** (manual-only).
- The flows connect directly to the **local** `videos` Postgres for pipeline state; Prefect API only
  tracks run state.

**Critical mental model:** `max_active_runs: 1` is **per-deployment**, not global. The work pool had
**no global `concurrency_limit`** (default unlimited). So the worker ran up to 9 flow-runs at once,
each spawning ffmpeg/yt-dlp → dozens of CPU-bound procs on 2 cores → death spiral.

---

## 2. The 136/145 `FAILED` investigation (DONE, root-caused)

`FAILED` is a **permanent dead-end**: every claim gate selects `status IN ('PENDING','PROCESSING',…)`
and excludes `FAILED`, so nothing ever retries it. Three failure shapes:
- **audio_stuck** (~59): singer's `extract_audio_ffmpeg` had a hard **180s timeout** and transcoded
  the *whole* track; long videos time out → `TimeoutExpired` → generic `except` → `mark_failed`.
- **transcript_stuck** (~46–56): live/no-caption videos; scribe is caption-first; loader raised
  non-`TranscriptExtractionError` → hit generic `except` → `mark_failed` (dead-end) instead of safe.
- **visuals_stuck** (~30): painter frame grabs are O(1) seeks, not duration-limited; stranded purely
  by the `FAILED` dead-end.

### Fixes shipped (local, uncommitted)
- `maia/singer/flow.py`: `SINGER_CHUNK_SECONDS=1800`; `store_audio_task` rewritten to return
  `list[(video_id, vault_rel_path, audio_bytes)]` and **chunk** long videos (`_plan_audio_chunks`)
  so each ffmpeg call stays under timeout; `TimeoutExpired`/exception → `release_to_pending`
  (non-fatal). `VideoRepository` instantiated lazily (happy path never opens a DB conn).
- `maia/scribe/flow.py`: `SCRIBE_MAX_DURATION_SECONDS=1800`, `SCRIBE_LONG_VIDEO_MESSAGE`,
  `TranscriptTooLongError`; `_transcribe` treats ANY loader exception as "no captions" (re-raises
  `QuotaExhaustedError` so backoff still works); >30 min no-caption → templated apology +
  `mark_transcript_safe` (no STT). Generic loader error → safe, not `FAILED`.
- `maia/media/streamer.py`: added `extract_audio_chunk` (ffmpeg `-ss`/`-t`).
- `maia/heartbeat/flow.py`: `heartbeat_flow` is `@flow`; originally `FLEET_UNITS=["prefect-worker"]`.

### Production recovery EXECUTED (this VPS = prod)
- Pre-reset: **145 FAILED** (audio 59, transcript 56, visuals 30).
- Surgical reset: `UPDATE videos SET status='PENDING' WHERE status='FAILED'` for all 145 (did NOT
  use the blunt `tools/recover_failed_runs.py --apply`/frame-purge, to avoid 115 needless frame
  regenerations). Verified `FAILED → 0`.
- Triggered manual singer/scribe/painter runs; confirmed new code live (log:
  `Audio extraction timed out for w3tR6h6C2TI (chunk 0) — releasing for retry`).
- After first cycles `FAILED` dropped to **6** (genuine `AudioExtractionError`s — appropriate).

### Test migration (DONE)
Updated 5 tests to new behavior; fixed a mis-indent (call outside `with` patch block). Full maia
suite **110 pass**, ruff clean.

---

## 3. Concurrency / CPU-saturation audit (DONE, fixes applied + pending)

**Root cause of the 48 `FAILED` stagnation + load 26–27:** no global concurrency ceiling. 9
deployments × `max_active_runs:1` = up to 9 concurrent flow-runs, each spawning ffmpeg/yt-dlp.
Evidence: 6 concurrent singer `ffmpeg` = 6 singer runs (per-deployment cap not effectively
enforced). Per-flow internal concurrency was ALREADY bounded (singer/painter/streamer
`MAX_CONCURRENT_VIDEOS=1`; scribe `MAX_CONCURRENT_TRANSCRIPTS=1`; painter extracts frames serially).
Verified: only ONE `prefect-worker`, no legacy `pleiades-*` units, no duplicate procs.

### Fixes APPLIED (live, no API needed)
- **systemd cgroup backstop** on `prefect-worker.service`: `CPUQuota=180%` + `TasksMax=120`.
  `daemon-reload` + worker restarted. Confirmed in cgroup (`cpu.max=180000 100000`, `pids.max=120`).
  Box can NEVER be fully saturated regardless of Prefect misconfig. This is the safety net.
- **Heartbeat now reports the WHOLE fleet** (user request): `heartbeat/flow.py` adds
  `collect_fleet_status()` — queries the Prefect API for each of the 9 deployments' last flow-run
  state → "Deployments" embed field, plus executor `prefect-worker` unit → "Executor" field.
  Degrades to "API unreachable" if control plane down. `FLEET_DEPLOYMENTS` enumerates the 9
  (muralist excluded). Tests updated (3 pass); full maia suite 110 pass; ruff clean.

### Fixes PENDING — need the micro (user powered it off)
1. **Set `default` work-pool global `concurrency_limit = 2`** (PRIMARY fix):
   `prefect work-pool set-concurrency-limit default 2` (or client `update_work_pool(p.id, concurrency_limit=2)`).
2. **Re-apply deployments from `prefect.yaml`** (`prefect deploy -n <name>` per deployment) to
   guarantee `max_active_runs: 1` is actually set (6-concurrent-singer evidence implies it wasn't).
3. Re-run the surgical FAILED reset (currently 48 = 30 audio_stuck + 12 muralist + 6 visuals_stuck).

---

## 4. The 12 "all-done" `FAILED` = muralist (separate bug, NOT concurrency)

- 12 FAILED videos have audio/transcript/visuals all `DONE` but `has_video=FALSE`,
  `clip_phase=PENDING` → only the **muralist** claim gate (`claim_muralist_batch` requires
  `has_video=FALSE`) can pick them up. Every other flow's claim gate excludes them.
- **Muralist is NOT automated** (no deployment/cron/systemd; confirmed by grep of logs/history/
  `prefect deployment ls`). It can only run via manual `python -m maia.muralist.flow` (has
  `main()`/`__main__`) or the agent CLI (`registry.py` maps `"muralist": MuralistAgent`).
- Conclusion: a **manual muralist run** (likely during refactor/migration testing) failed on clip
  generation for those 12 and called `mark_failed`. They are otherwise complete (audio+visuals+
  transcript) and arguably should be `PROCESSED`. Muralist failure shouldn't strand them.
- Out of scope for the concurrency fix; needs a separate look at muralist clip failures (and
  possibly treating the clip stage as non-blocking for `PROCESSED`).

---

## 5. Bring-up runbook (when micro is restarted)

```
# on micro (control plane) — set the global cap (PRIMARY fix)
prefect work-pool set-concurrency-limit default 2
# re-apply deployments so max_active_runs:1 is guaranteed on each
cd /home/ubuntu/code/pleiades
for d in streamer singer painter scribe hunter tracker archeologist heartbeat janitor; do
  prefect deploy -n "$d"   # reads prefect.yaml (max_active_runs:1, schedules, env)
done
# on executor (this VPS) — worker already has CPUQuota/TasksMax backstop
sudo systemctl restart prefect-worker
# re-run the surgical FAILED reset (48 currently) so they reprocess under bounded concurrency
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
```

---

## 6. Important constraints / environment notes

- This VPS has the **local Postgres** (`DATABASE_URL=postgresql://pleiades:***@127.0.0.1:5432/pleiades`).
  The Prefect *orchestration* tables (deployments, work_pools, flow_runs) live in the **micro's
  SQLite**, NOT this Postgres — so you cannot set work-pool limits via `psql` here; must use the API.
- `systemctl` shows only `bgutil-provider` (running), `pleiades-hf-cache-evict` (dead),
  `prefect-worker` (running). The 9 old `pleiades-*` agent units are gone (stopped in Phase 4).
- Python env: `.venv` at `/home/ubuntu/code/pleiades/.venv`. Run tests with
  `source .venv/bin/activate && python -m pytest maia/tests`. Need `PREFECT_API_URL` +
  `PYTHONPATH=atlas/src:maia/src` for Prefect CLI.
- The `tools/recover_failed_runs.py` tool exists (dry-run + `--apply` = `reset_failed_to_pending()`
  + frame purge) but was deliberately NOT used for the recovery (too blunt — would force 115
  needless frame regenerations).

---

## 7. Relevant files (current session)

- `prefect.yaml` (untracked) — 9 deployments, `max_active_runs:1`, intervals, env.
- `/etc/systemd/system/prefect-worker.service` — **edited**: `CPUQuota=180%`, `TasksMax=120`.
- `maia/src/maia/singer/flow.py` — chunked extraction, non-fatal timeout.
- `maia/src/maia/scribe/flow.py` — 30-min graceful / templated apology.
- `maia/src/maia/media/streamer.py` — `extract_audio_chunk`.
- `maia/src/maia/heartbeat/flow.py` — whole-fleet Discord reporting (`collect_fleet_status`,
  `FLEET_DEPLOYMENTS`, `FLEET_UNITS`).
- `maia/tests/test_heartbeat.py`, `test_scribe.py`, `test_singer.py` — updated for new behavior.
- `atlas/src/atlas/repositories/video/state_machine.py` — claim gates (exclude `FAILED`);
  `reset_failed_to_pending()` exists; `mark_audio_safe`/`mark_transcript_safe`/`mark_visuals_safe`
  latch `PROCESSED` only when the other two `has_*` flags are set (and `WHERE <phase> <> 'DONE'`).
- `maia/src/maia/muralist/flow.py` — manual-only; claims `has_video=FALSE`; marks FAILED on failure.

---

## 8. Status of work

- **Committed (early):** P1a, CI/mypy, P1b PROCESSED fix, etc. (see git log).
- **Uncommitted local:** singer/scribe/streamer/heartbeat flow changes, 3 test files, `prefect.yaml`,
  and the systemd unit edit. **Not pushed** — push only on approval.
- **Verified:** maia 110 tests pass, ruff clean, cgroup backstop live, heartbeat fleet code done.
- **DONE 2026-07-13:** micro powered on (OCI CLI); work-pool `concurrency_limit=2` set; 9 deployments
  reapplied (`prefect deploy --all`); worker restarted; orphan flow-run slots cleaned; micro SQLite
  `timeout=30` patch applied; FAILED backlog = 0 (draining PENDING/PROCESSING at concurrency 2).
- **Open bug:** 12 muralist-stranded "all-done" videos (in PROCESSING, has_video=FALSE,
  clip_phase=PENDING) — muralist is manual-only; decide whether to make clip stage non-blocking for
  PROCESSED, or run muralist manually. Separate from the concurrency fix.
- **Optional hardening:** migrate Prefect orchestration DB to Postgres (kills the residual SQLite lock
  noise on the 1/8-OCPU micro), or raise `PREFECT_API_SERVICES_*_LOOP_SECONDS`. Current state is
  functional and bounded.
