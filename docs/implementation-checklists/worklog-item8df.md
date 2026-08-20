# Worklog — Item 8d & 8f

## 8d. Remove dead workflows

Checked repo (grep for each workflow filename). The only references to
`data_collector.yml`/`ml-ci`/`ml-cd` are in documentation that explicitly
labels them dead/legacy and instructs removal (`.opencode/agent/docs-cicd.md`,
`docs/implementation-checklists/cleanup-plan.md`,
`.agents/skills/pleiades-cleanup/SKILL.md`) — no live triggers. Deleted.

| File | Action | Justification |
|------|--------|---------------|
| `.github/workflows/data_collector.yml` | Deleted (`git rm`) | Schedule commented out → dispatch-only; known-broken `./collector` path (no such dir). Only doc references. |
| `.github/workflows/ml-ci.yml` | Deleted (`git rm`) | Legacy ML stack (`hf-spaces/`, `training/`). Replaced by `ci.yml`/`build-env.yml`. Only doc references. |
| `.github/workflows/ml-cd.yml` | Deleted (`git rm`) | Legacy HF deploy. Its `workflow_run` trigger pointed at the deleted `ml-ci.yml` ("CI - Lint & Test"). Superseded by `cd.yml`. Only doc references. |
| `.github/workflows/train_build.yml` | Kept; edited | No schedule (push+dispatch). Added "manual/dispatch-only" note. Fixed path filter `build-training-image.yml` → real workflow `build-env.yml`. |
| `.github/workflows/train_daily.yml` | Kept; edited | Schedule commented out → dispatch-only; added note. |
| `.github/workflows/train_monthly.yml` | Kept; edited | Schedule commented out → dispatch-only; added note. |
| `.github/workflows/train_weekly.yml` | Kept; edited | Schedule commented out → dispatch-only; added note. |

## 8f. Compose secret mismatch

Code reads `YOUTUBE_API_KEY_POOL_JSON` (atlas, maia/alkyone, tools, mcp);
grep confirms nothing reads the old `YOUTUBE_API_KEYS` name. `docker-compose.yml`
passed `YOUTUBE_API_KEYS: ${YOUTUBE_API_KEYS}` in 3 services (hunter, tracker,
archeologist). Changed all 3 to `YOUTUBE_API_KEY_POOL_JSON: ${YOUTUBE_API_KEY_POOL_JSON}`
(env interpolation preserved, no secrets hardcoded). `docker-compose.live.yml`
contains no YOUTUBE vars — no change needed there.

## Validation

- `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('OK')"` → `OK`
- `grep -rn 'YOUTUBE_API_KEYS' docker-compose*.yml` → no matches
