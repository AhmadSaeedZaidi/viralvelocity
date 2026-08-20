# Worklog — Item 4(a): Safe Deletions

Branch: `wip/adaptive-scheduling-and-mcp`. Scope: cleanup-plan.md §(a) table — provably dead code only.
Source-only deletions; live DB and running orchestrator untouched.

## Deletions (each verified zero live references via repo-wide grep incl. tests/tools)

### maia — legacy flow twins (dead task wrappers)
- `maia/src/maia/hunter/flow.py:378-381` — `ingest_results` legacy `@task`. Dead: only definition; tests use `ingest_results_task`. Verified: `grep -rn "ingest_results\b"` → only def + test docstrings.
- `maia/src/maia/tracker/flow.py:224-229` — `update_stats` legacy `@task`. Dead: only definition; tests use `update_stats_task`. Verified: `grep -rn "update_stats\b"` → only def + test docstrings.
- `maia/src/maia/scribe/flow.py:245-250` — `process_transcript` legacy `@task`. Dead: only definition; tests use `process_transcript_task`. Verified: `grep -rn "process_transcript\b"` → only def + test docstring.

### atlas/utils.py — dead helpers
- `retry_async` (was 23-59) — zero references anywhere (no tests/tools). Removed.
- `health_check_all` (was 62-74) — zero references. Removed.
- `execute_youtube_request_async` (was 306-313) — zero references. Removed.
- Removed now-unused `import asyncio`, `import functools`.

### atlas/vault.py — dead accessors
- `reset_vault` (was 611-614) — zero references. Removed.
- `make_uri` — abstract (VaultStrategy) + HF impl + GCS impl — zero code references (only atlas/README.md doc). Removed all three.
- `store_binary` — abstract + HF impl + GCS impl — zero code references (only atlas/README.md doc). Removed all three.
- KEPT `store_json`/`fetch_json` — used internally by `store_metadata`/`fetch_metadata`/`fetch_transcript` (all live).

### atlas/egress.py — dead EgressPool methods
- `size`, `has_multiple`, `labels`, `next_after`, `shuffled_order` — zero references (only `cycle()` used by streamer.py). Removed all five + now-unused `import random`.
- KEPT `parse_proxy_pool`, `proxy_labels`, `cycle`, `DIRECT` — used internally / by streamer.

### atlas/repositories — dead channel/ingestion methods
- `atlas/repositories/channel.py` — `save` (was 12-35) and `log_stats` (was 67-82): zero callers (hunter uses `ingest_channel_snapshot`). Removed.
- `atlas/repositories/video/ingestion.py` — `save` (was 27-66): zero callers. Removed.
- `atlas/repositories/video/protocols.py` — `save` protocol declaration (was 21): removed to match.

### maia/orchestrator.py — 12 pre-existing ruff errors resolved (no live code deleted)
- F401 `typing.Coroutine` unused → removed from import.
- UP035 `Callable` → moved to `collections.abc`.
- E402 ×9 (flow imports after `logger`) → moved flow imports to top of file (they ARE used by `build_specs`; relocating fixes E402 without deleting live code).
- W292 → added trailing newline.
- Verified `python -c "import maia.orchestrator"` still works.

## KEPT (deliberately, with reason)
- `run_hunter_cycle`, `run_tracker_cycle`, `run_archeology_campaign` — the orchestrator's `build_specs()` imports and calls these (live process). NOT dead. Deleting would break the running orchestrator + `test_orchestrator.py` (asserts 9 agents).
- `janitor_cycle`, `run_painter_cycle`, `run_scribe_cycle`, `hunt_history` — referenced by `__init__.py` exports, alkyone tests, and/or `tools/run_live_pipeline.py`. NOT dead.
- State-machine methods `begin_step`/`mark_step_phase`/`get_pipeline_phase`/`mark_done` — referenced by `atlas/tests/test_state_machine.py` + alkyone tests. Per "zero references incl. tests" rule → KEEP.
- `COMPLIANCE_MODE` — live in `atlas/config.py` + tested. KEEP.
- `validate_youtube_id`/`validate_channel_id` — referenced by `atlas/tests/test_utils.py`. KEEP.
- `vault` lazy `__getattr__` shim — documented backward-compat API (docs/adaptive-scheduling.md). KEEP (low-risk).
- `T = TypeVar("T")` in utils.py — pre-existing, not ruff-flagged, out of scope.

## Remaining ruff (maia/) — 5 errors, all code-style, NOT dead code (left per instruction)
- `janitor/flow.py:180` I001 (import block unsorted), `:190`/`:251` UP017 (`datetime.now(timezone.utc)` → `datetime.UTC`).
- `quality/thresholds.py:45` E501 (line too long).
- `tracker/flow.py:167` E501 (line too long).
These are legitimate style violations, not dead imports → left + noted.

## Verification
- `.venv/bin/python -m pytest atlas/tests -q` → **71 passed**.
- `.venv/bin/python -m pytest maia/tests -q` → **164 passed**.
- `.venv/bin/python -m pytest mcp/tests -q` → **11 passed**.
- `.venv/bin/python -m ruff check maia/` → **17 → 5** (12 orchestrator resolved; 5 style remain, see above).
- `python -c "import maia.orchestrator"` → OK.
- All modified modules import cleanly.