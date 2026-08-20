# Worklog Item 8a — Harden CD so failing CI cannot deploy

## File changed
`.github/workflows/cd.yml`

## What changed
1. **Added a `smoke` preflight job** (`cd.yml` `jobs.smoke`) that runs BEFORE the
   deploy job. It is wired to `deploy` via `needs: smoke`, so a failing or never-passing
   preflight blocks deployment. It contains two steps:
   - **Require CI to pass on this commit** (runs only on `push`): polls the GitHub
     check-runs API for the current head SHA and gates on the four core CI jobs from
     `ci.yml` — `Check for dependency changes`, `Build CI Environment`,
     `Quality (Linting & Type Checking)`, and `Unit Tests`. Fails the job (and thus
     blocks deploy) if any of them ended in a non-success conclusion, or if it times out.
     Manual dispatches (`workflow_dispatch`) have no upstream CI run for the SHA, so the
     gate is skipped for them.
   - **Code syntax smoke**: a lightweight local `python3 -m compileall` on the Python
     sources (atlas, maia, alkyone, mcp, tools) before anything is deployed.
2. **Made the `deploy` job depend on the preflight**: added
   `needs: smoke` to `jobs.deploy`.

## Why
`ci.yml` runs its own workflow in parallel; GitHub Actions has no native cross-workflow
`needs:`. The check-runs API gate gives us a real, workable dependency on CI results,
ensuring a failing CI cannot trigger an SSH deploy.

## Validation
- `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/cd.yml'))"` parses OK
  (`jobs` keys: `smoke`, `deploy`).
- No other workflows/files touched. No secrets altered. No real deployments run.