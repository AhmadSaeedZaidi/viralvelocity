# Deployment — Two-VPS Architecture

This guide explains how Pleiades is deployed across **two machines**: a tiny
Oracle **control plane** (the Prefect server) and a **2-core executor VPS** that
runs the agent fleet and the production databases. No API keys, tokens, or
passwords are written here — everything is injected via environment variables
and `.env` files that are **never committed** (see §5).

> For day-to-day operations, incident playbooks, and troubleshooting, see
> [`micro-prefect-orchestration.md`](micro-prefect-orchestration.md). This file is
> about *standing the system up* and *reproducing it after a rebuild*.

---

## 1. Topology

| Role | Machine | Runs |
|------|---------|------|
| **Control plane (micro)** | Oracle `e2-micro-server` (private `10.0.0.22`, public IP static) | Prefect **server/API** on `:4200` (SQLite at `/opt/prefect/prefect.db`) |
| **Executor (this VPS)** | 2-core VPS (`10.0.0.6`) | one `prefect-worker` service pulling the `default` pool; runs the flows (ffmpeg / yt-dlp / STT) |

- The flow code (`maia/`, `atlas/`) lives on the **executor** and is executed there.
- The micro only stores **orchestration** state (deployments, schedules, flow-run
  state) — **not** video data.
- The live **video** DB is `pleiades` @ `127.0.0.1:5432` on the executor (PostgreSQL,
  separate from the micro's SQLite).
- The HuggingFace **vault** is the cold tier (frames / audio / transcripts /
  metadata), written by the agents on the executor.

### Why two VPSes?
The control plane is a free/cheap always-on box that survives reboots and keeps
the schedule + run history. The executor does the CPU-heavy work. Splitting them
means a Prefect server restart or an executor crash never loses orchestration
state, and the executor's cgroup backstop can throttle runaway concurrency
without taking the scheduler down.

---

## 2. Prerequisites

On **both** machines:
- Python 3.11+ (the executor uses a venv at `/home/ubuntu/code/pleiades/.venv`)
- `git`, `pip`, `systemd`
- Network reachability between them (the executor must reach `10.0.0.22:4200`)

On the **executor** specifically:
- **Deno** ≥ 2.3.0 on `PATH` (`/usr/local/bin/deno`) — required by `yt-dlp` to solve
  YouTube's BotGuard / PoToken JS challenge.
- **FFmpeg** — frame extraction + audio chunking.
- **bgutil Po-token provider** — a Node HTTP server on `127.0.0.1:4416` (systemd unit
  `bgutil-provider`). YouTube (2026) requires a per-video Proof-of-Origin token for
  authenticated GVS/subtitle requests.
- A **PostgreSQL 15+** instance for the `pleiades` video DB.
- A **HuggingFace** account + write token for the vault dataset.

On the **micro**:
- A static private IP (Oracle reserves it; the instance always boots back to
  `10.0.0.22`).
- `prefect` installed; the server is launched by `prefect-server.service`.

---

## 3. Control plane (micro)

1. Install `prefect` (same version as the executor, currently **3.7.7**).
2. Create `/etc/systemd/system/prefect-server.service` with the key line:
   ```
   Environment=PREFECT_SERVER_DATABASE_CONNECTION_URL=sqlite+aiosqlite:////opt/prefect/prefect.db?timeout=30
   ```
   The `?timeout=30` raises SQLite's `busy_timeout` from 5s → 30s so writes retry
   instead of 503-ing under contention. **Do not omit this** — a 1/8-OCPU box on
   SQLite will otherwise lock-storm.
3. `sudo systemctl enable --now prefect-server.service`.
4. Verify: `curl -s http://10.0.0.22:4200/api/health` → `{"status":"ok"}`.

---

## 4. Executor VPS

### 4.1 Code + worker

```bash
git clone <repo> /home/ubuntu/code/pleiades
cd /home/ubuntu/code/pleiades
python -m venv .venv && source .venv/bin/activate
# install atlas + maia (+ dev) — poetry or pip per Makefile
make install            # or: (cd atlas && poetry install --with dev --all-extras) ; (cd maia && poetry install --with dev)
make -C atlas setup     # provision the video DB schema (schema.sql)
```

Create `/etc/systemd/system/prefect-worker.service`:
```
[Service]
Environment=PREFECT_API_URL=http://10.0.0.22:4200/api
Environment=PYTHONPATH=atlas/src:maia/src
# CPU/process backstop so a Prefect misconfig can never fully saturate the box:
CPUQuota=180%
TasksMax=120
ExecStart=/home/ubuntu/code/pleiades/.venv/bin/prefect worker start --pool default --type process
```
`CPUQuota=180%` caps the worker at ~1.8 cores; `TasksMax=120` caps threads/processes.
These are defense-in-depth — the real concurrency cap is the Prefect work pool (§4.3).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now prefect-worker
```

### 4.2 bgutil Po-token provider

The bgutil provider runs as a **separate** service (`bgutil-provider`), because
yt-dlp's plugin needs a live HTTP server for PoTokens:
```
ExecStart=/usr/bin/node /home/ubuntu/bgutil-ytdlp-pot-provider/server/build/main.js
```
Verify: `yt-dlp -v <url> | grep 'PO Token Providers'` should list `bgutil:http`.
The stale `yt-dlp-get-pot` package **conflicts** with the built-in framework —
keep it uninstalled.

### 4.3 The 9 deployments + work queues

`prefect.yaml` declares **9 deployments** (one per agent): `streamer, singer,
painter, scribe, hunter, tracker, archeologist, janitor, heartbeat`. Muralist is
**intentionally not** a deployment (manual-only).

> **Concurrency model (critical).** In Prefect 3.7.7 the per-deployment
> `max_active_runs` / `Deployment.concurrency_limit` fields are **silently ignored**.
> The *only* working per-deployment isolation is the **work-queue `concurrency_limit`**.
>
> - Work pool `default`: global `concurrency_limit = 9` (one slot per queue).
> - One work queue per deployment, each `concurrency_limit = 1`
>   (`heartbeat` priority 1, all others priority 2).
>
> Effect: each agent runs ≤1 instance, and all 9 can run concurrently. Priorities
> exist only for UI ordering. **The pool limit must be ≥ the number of queues that
> need a slot** — a limit of 5 once starved the streamer (the pipeline input) and
> stalled ingestion of 23k+ videos.

To (re)create the whole topology reproducibly after a micro rebuild:
```bash
export PREFECT_API_URL=http://10.0.0.22:4200/api
python tools/setup_orchestration.py     # idempotent, no secrets: pool limit=9 + 9 queues (limit=1)
prefect deploy --all                    # applies prefect.yaml (schedules, env, work_queue_name)
sudo systemctl restart prefect-worker
```
`tools/setup_orchestration.py` reads `PREFECT_API_URL` from the environment and
contains **no secrets**. It sets the pool global `concurrency_limit=9` and creates
/updates all 9 queues with `limit=1`.

### 4.4 CI/CD

`cd.yml` SSH-deploys to the executor on push to `main` (or manual dispatch):
`git pull --ff-only` → install → `python tools/setup_orchestration.py` →
`sudo systemctl restart prefect-worker`. Required GitHub secrets:
`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_PATH`, `WORKER_SERVICE`.

---

## 5. Secrets handling (no keys in the repo)

- **Never commit** `.env`, cookies files, or tokens. `.gitignore` already excludes them.
- Credentials come from **environment variables** (Twelve-Factor). The code reads
  them via `atlas.config.settings` (Pydantic `BaseSettings`).
- Template files that **are** committed:
  - root `.env.example`
  - `atlas/ENV.example`, `maia/ENV.example`
  Copy one of these to `.env` and fill in real values. The executor's live `.env`
  is created out-of-band and never pushed.
- YouTube API keys live in `YOUTUBE_API_KEY_POOL_JSON` (a JSON array of key strings).
- The HF vault token is `HF_TOKEN`; the vault repo id is `HF_DATASET_ID`.
- YouTube cookies (for authenticated/PoToken sessions) are supplied via
  `YOUTUBE_COOKIES_PATH` (Atlas reads that var, **not** `MAIA_COOKIES_PATH`). Export
  the cookies from a logged-in **incognito** session and close it without logging
  out, or YouTube invalidates them.
- The control-plane micro is reached over SSH / its private IP; its OCID and OCI
  config live only on the executor, never in the repo.

---

## 6. Verify a fresh deploy

```bash
export PREFECT_API_URL=http://10.0.0.22:4200/api
prefect work-pool inspect default        # concurrency_limit = 9
prefect work-queue ls                    # 9 queues, each limit = 1
prefect deployment ls                    # 9 deployments, status Ready
systemctl status prefect-worker         # active (running)
# trigger one cycle to confirm the fleet is live:
prefect deployment run heartbeat_cycle/heartbeat
```
