# Worklog — Cleanup Item 7 (Docs fixes)

Branch: `wip/adaptive-scheduling-and-mcp`. Scope: `cleanup-plan.md` Item 7 §(c) docs
only. No `.github/workflows/`, code, or tests were touched.

## Changes made

| File | What changed |
|------|-------------|
| `docs/README.md` | Fixed 3 broken `challenges.md` → `CHALLENGES.md` link/texts (Development & Ops bullet, For Operators step, Project-structure comment). |
| `docs/testing.md` | Rewrote §CI/CD: replaced the stale "Neon Postgres ephemeral branches" model with the ACTUAL model — per-PR `ci.yml` (`check-changes`/`build-env`, `quality` = `make -C {atlas,maia,alkyone,mcp} lint` = ruff+mypy, `unit-tests` = `make test-unit`), on-demand guarded `alkyone.yml` (manual dispatch, `ALKONE_TEST_*` secrets, `guard.py` prod interlock), and `cd.yml` SSH deploy. Added local-gates snippet (`make lint` / `make test-unit`). |
| `docs/resiliency-strategy.md` | Replaced the obsolete `HUNTER_KEY_POOL`/`TRACKER_KEY_POOL` env-var snippet with the real model: single `YOUTUBE_API_KEY_POOL_JSON` env var → `settings.key_rings` split into `hunting` / `tracking` / `archeology` rings (sizes via `KEY_POOL_TRACKING_SIZE` / `KEY_POOL_ARCHEOLOGY_SIZE`), `KeyRing("hunting")` / `KeyRing("tracking")`; documented the pool invariant (hunter keys must exhaust before tracker keys) and CHAOS MODE fallback. Naming confirmed in `atlas/src/atlas/config.py` (`key_rings`, lines 262–289) and `atlas/src/atlas/utils.py` (KeyRing, `settings.key_rings`). |
| `docs/micro-prefect-orchestration.md` | Resolved internal service-name drift: updated the §1 topology row from "one `prefect-worker` service" to the live active unit "one `prefect-orchestrator` service running `maia.orchestrator` in-process". The runbook now consistently uses `prefect-orchestrator` as the ACTIVE service; `prefect-worker` remains referenced only as the disabled legacy/rollback unit (§4, §8). |
| `docs/architecture.md` | Added a minimal, accurate "Deployed" note under Tracker Features reflecting adaptive-scheduling deployed state (2026-08-03 live `pleiades` DB; `watchlist` tier `HOURLY` → `DAILY` → `WEEKLY` decay via `calculate_next_track_time`; `update_schedule` advances `next_track_at`; tracker drives cycles via `fetch_targets_task`/`update_stats_task`). |
| `KEYS.md` | Corrected the header — the file IS tracked/committed, so the old "(gitignored — never commit this file)" claim was wrong. New header states it is tracked and MUST never contain secret material. |
| `SUMMARY.md` (gitignored, untracked) | **SCRUBBED** the plaintext Mistral API key from line 19. The literal key value was removed and replaced with a pointer to `.env` (`MISTRAL_API_KEY`). Rest of the file left intact. Not `git add`-ed. Confirmed scrub via grep (key no longer present anywhere in the repo). |

## Item 5 — root scratch dedup (recommendation only, files left as-is)

- `summary.md` — **tracked** (committed), 18 KB, the actual session/architecture
  summary.
- `SUMMARY.md` — **gitignored** (`.gitignore` line 120, untracked), the local
  session notes that contained the Mistral key.
- THESE ARE DIFFERENT FILES. Recommendation: keep `summary.md` as the tracked
  doc; keep `SUMMARY.md` gitignored if still needed locally, or fold any
  remaining useful content into `summary.md` and delete `SUMMARY.md`. No
  consolidation was performed in this change.

## Item 8 note — `train_build.yml` path filter (NOT edited — another agent's scope)

`train_build.yml`'s path filter references a non-existent
`build-training-image.yml` (the checked-in workflow is `build-env.yml`). Flagged
for the CI/CD hardening item; not edited here per scope.

## Service-name drift resolution & residual note

- `cd.yml` (un-editable here) and `docs/deploy.md` name the deploy target
  `prefect-worker` (`WORKER_SERVICE` default).
- Live box: the ACTIVE/running systemd unit is `prefect-orchestrator.service`
  (`ExecStart=… -m maia.orchestrator`); `prefect-worker.service` still exists but
  is stopped/disabled (rollback path).
- Ops runbook (`docs/micro-prefect-orchestration.md`) was reconciled to the
  **live** service name (`prefect-orchestrator`), since its commands must work on
  the running system; the residual `prefect-worker` naming in `cd.yml` /
  `deploy.md` is a genuine CI/CD concern. **Recommendation (Item 8):** point
  `cd.yml` `WORKER_SERVICE` at the `prefect-orchestrator` unit so CI restarts the
  live service, then re-align `deploy.md` to match.

## Verification

- All doc links in the edited files resolve (verified against filesystem from
  `docs/`): `README.md` and `testing.md` link targets all exist.
- Key-pool naming confirmed against code (`config.key_rings`,
  `utils.KeyRing`).
- Mistral key scrub verified with grep (key string absent repo-wide).

## Files NOT modified (out of scope / forbidden)

`.github/workflows/*` (incl. `ci.yml`, `alkyone.yml`, `cd.yml`, `train_build.yml`,
`build-env.yml`), all code and test files. Pre-existing working-tree changes
(test files) by other agents were left untouched.