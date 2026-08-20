# Worklog Item 8b — Fix CI image bootstrap chicken-and-egg for fresh clones

## File changed
`.github/workflows/ci.yml`

## Problem
The container jobs (`quality`, `unit-tests`) unconditionally pull the
`ci-env:latest` image from GHCR, but `build-env` only builds+pushes it when the
`check-changes` path filter reports a dep change. On a **fresh clone** where
`ci-env:latest` was never pushed to the registry, no dependency changed, so
`build-env` was skipped and the container jobs failed pulling a non-existent
image — the CI has no way to self-bootstrap.

## What changed
1. **Added an `ensure-image` preliminary job** (before `build-env`). It runs one
   step that inspects the registry (`docker manifest inspect $CI_IMAGE`) and
   emits an `image-ready` output:
   - `exists=true` when the image is present,
   - `exists=false` when missing (or uninspectable) — the self-heal trigger.
2. **Widened the `build-env` trigger**: it now also fires when
   `image-ready == "false"`, in addition to the existing dep-change condition:
   ```
   if: needs.check-changes.outputs.needs-build == "true" || needs.ensure-image.outputs.image-ready == "false"
   ```
   `build-env` reuses the existing `build-env.yml` reusable workflow (no new/duplicate
   build logic — same one Dockerfile build+push).
3. **Made `quality` depend on `ensure-image`** too:
   `needs: [build-env, ensure-image]`. Its skip/success guard is unchanged, so it runs
   when either the build succeeded or was correctly skipped. `unit-tests` already
   depended on `quality`, inheriting the fix transitively.

## Result
On a fresh clone: `ensure-image` finds no image → `build-env` builds+pushes
`ci-env:latest` → `quality`/`unit-tests` pull it successfully. On a warm repo with
deps unchanged: the image exists → `build-env` skipped → container jobs still run.

## Validation
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); yaml.safe_load(open('.github/workflows/build-env.yml')); print('OK')"` → `OK`.
- No other workflows/files touched. No secrets altered. No real deploys.