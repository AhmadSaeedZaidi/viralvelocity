# Worklog — Item 8e: Config drift unify

Interim owner did this manually (subagent dispatch returned empty).

## Drift found
- ruff `select` sets are already IDENTICAL across root/atlas/maia/alkyone/mcp
  (`E, W, F, I, N, UP, B`), so no ruff drift actually existed.
- mcp was the only package with NO `[tool.mypy]` block and no mypy in its
  Makefile lint, while atlas/maia (strict) and alkyone (non-strict) had it.
- pre-commit commit-stage ruff ran for atlas/maia/alkyone but NOT mcp.

## Changes
1. `mcp/pyproject.toml` — added `[tool.mypy]` matching the alkyone
   non-strict profile (`strict=false`, `warn_return_any`, `warn_unused_configs`).
   Chose non-strict over atlas-strict because mcp currently has 3 genuine
   `no-any-return` errors (`summarize.py:47,122`, `server.py:84`).
2. `.pre-commit-config.yaml` — added `ruff check (mcp)` and
   `ruff format (mcp)` hooks to the commit-stage section, mirroring the
   atlas/maia/alkyone entries.

## Intentional NOT done (additive/safe, suite stays green)
- NOT wired mypy into `mcp/Makefile` lint or pre-push: existing 3
  `no-any-return` errors would fail `make lint`. Config block is present so a
  future pass can add real type annotations and flip it on.
  Follow-up tracked.

## Verification
- `.pre-commit-config.yaml` parses; mcp has check+format in commit-stage.
- `mypy --config-file mcp/pyproject.toml` reports exactly the 3 known errors
  (no new ones).
- `ruff check src tests` in mcp: all checks passed.
- `ruff format --check` reports 3 mcp files would be reformatted (benign —
  auto-fixed on commit, consistent with other packages).