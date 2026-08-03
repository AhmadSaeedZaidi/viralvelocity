# Cleanup & Hardening Plan (a–d)

Status: DRAFT for sign-off. Branch `wip/adaptive-scheduling-and-mcp`. Live DB is READ-ONLY for all of this.
In-flight WIP committed as `5122ad9` — cleanup will branch from there and not clobber it.

Verification bar (per user): test-first — review tests, map to product vision, add tests for
untested core behaviors BEFORE refactoring the code they cover. Each step keeps `make test-unit`
green; integration (`alkyone`) runs guarded, never against prod.

---

## (a) Code cleanup — highest-value removals (audited, file:line cited)

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| 1 | 6 legacy `Legacy … wrapper` flows/tasks duplicate modern Prefect entrypoints (hunter flow.py:379-394, tracker flow.py:224-246, archeologist flow.py:176-192, janitor flow.py:557-571, painter flow.py:408, scribe flow.py:245-276) | major | Delete legacy twins; keep modern `@flow` symbols |
| 2 | Dead state-machine methods `begin_step`/`mark_step_phase`/`get_pipeline_phase`/`mark_done` (state_machine.py:223,227,241,303) | major | Delete + cascade |
| 3 | Dead `atlas/utils.py` helpers (`retry_async`, `validate_youtube_id`, `validate_channel_id`, `health_check_all`, `execute_youtube_request_async`) | major | Delete |
| 4 | Dead `atlas/vault.py` accessors (`store_metadata`, `fetch_metadata`, `fetch_transcript`, `store_visual_evidence`) | major | Delete |
| 5 | Inert `COMPLIANCE_MODE` + never-read settings `YOUTUBE_COOKIES_CONTENT`, `SCRIBE_STORE_AUDIO` (config.py) | major/minor | Drop settings or wire up; drop |
| 6 | N+1: 9 scalar COUNT queries in tracking.py:128-151 + 6 in quality.py:46-75 | major | Collapse to single aggregate SQL |
| 7 | N+1: per-video `get_latest_stats` in janitor archive loop (janitor.py:104) | major | Batch query (LEFT JOIN LATERAL) |
| 8 | Blocking sync `vault.store_*`/`time.sleep` in async paths (vault.py:336,340,530,533) | major | Move to `asyncio.to_thread` / executor |
| 9 | Dead channel/ingestion `.save`/`.log_stats`; dead egress pool methods (`has_multiple`, `size`, `labels`, `next_after`, `shuffled_order`) | minor | Delete |
| 10 | 4 unused imports (orchestrator.py:23 `Coroutine`; tools F401s) | minor | Remove |
| 11 | Dead `audio_bytes`/`audio_pending` data path (never passed a value) | major | Wire or remove; decide in step 3 |
| 12 | Dead training helpers + F541 f-strings + stale comments + hardcoded `/var/lib/pleiades/agent_state.json` | minor | Clean |

Note: no commented-out `print`/`await` blocks found (good). No N+1 in watchlist velocity (bounded to batch).

## (b) DB → 3NF (audit + propose; NO live-DB writes)

Gap list (from static schema/models audit; table.column, rule, fix):

1. **videos state redundancy** — `has_*` booleans (schema.sql:47,50-51,57,66) duplicate 5 `*_phase`
   enums (schema.sql:296-300), kept in sync by trigger 341-375. FIX: drop booleans (keep phase enums
   as single source; derive booleans via generated columns if agents need them).
2. **`videos.status='PROCESSED'` is derived** (state_machine.py:134,150,256; transcript.py:87).
   FIX: generated column / view `PROCESSED ⇔ all phases DONE`; keep PENDING/PROCESSING/FAILED stored.
3. **`ARCHIVED` is an orthogonal retention fact** jammed into `status`. FIX: split `archival_state`
   (ACTIVE/ARCHIVED/HARD-DELETED) column.
4. **Transient staging on entity** — `audio_pending BYTEA` + `vault_write_pending` (schema.sql:71-72).
   FIX: `vault_pending(video_id, audio, created_at)` staging table; derive the flag from EXISTS.
5. **Raw-artifact metadata embedded** — `fetched/raw_uri/raw_stored_at` (schema.sql:57-63). FIX:
   `raw_artifacts(video_id PK/FK, uri, stored_at)`.
6. **`watchlist.last_tracked_at` duplicates `videos.last_tracked_at`** (schema.sql:154 vs 98). FIX:
   single source = `videos.last_tracked_at`; watchlist joins (already joins for published_at).
7. **`videos.tags TEXT[]`, `wiki_topics TEXT[]`** (1NF arrays). FIX: keep tags as array (documented
   decision) or split `video_tags`; DROP `wiki_topics` (write-only) + `default_language` (write-only).
8. **`transcripts.content JSONB` duplicates vault payload until flush.** FIX: `transcript_staging`
   table; `transcripts` = metadata only.
9. **`search_queue.result_count_total` drifts** (only-incremented, never reset). FIX: reset on
   re-add or derive from an append-only `search_results` log.
10. **Dead tables/cols**: `channel_history` (0 refs), `wiki_topics`, `default_language`. FIX: drop.
11. **Legacy DDL-less schema referenced** — training/data_loader.py + hf-spaces db_client.py query
    `videos.video_id/duration_seconds`, `video_stats`, `search_discovery`, `trending_discovery`
    (don't exist in schema.sql). FIX: alias to real tables or delete those code paths.

Non-issues (do NOT change): `video_stats_log`/`channel_stats_log` time series (clean), `pipeline_phase`
(stored but derived — acceptable), `system_events.payload` (append-only log).

DELIVERABLE: `schema.sql` rewrite + migration SQL + repo changes, all on a branch; plan to apply to
live DB only after your explicit approval and a backup.

## (c) Docs update

- Fix `docs/README.md` broken link `challenges.md` → `CHALLENGES.md`.
- `docs/testing.md` §CI/CD is STALE (Neon ephemeral branches) → rewrite to the actual on-demand
  guarded alkyone model.
- `docs/resiliency-strategy.md` shows `HUNTER_KEY_POOL`/`TRACKER_KEY_POOL` → update to
  `YOUTUBE_API_KEY_POOL_JSON` + `key_rings` naming.
- Service-name drift: `prefect-worker` (cd.yml, deploy.md, micro-prefect) vs `prefect-orchestrator`
  (checklist) → reconcile to one.
- Root scratch duplicates: `summary.md` (tracked) vs `SUMMARY.md` (gitignored) → consolidate/remove.
- `KEYS.md` says "gitignored — never commit" but IS committed → decide (move to gitignore or keep
  and fix header). ⚠️ `SUMMARY.md` contains a plaintext Mistral API key — scrub/rotate.
- `train_build.yml` path filter references non-existent `build-training-image.yml`.
- Update docs to reflect adaptive-scheduling already-deployed state (checklist is historical log).

## (d) CI/CD

LIVE today (not vestigial): per-PR `ci.yml` (lint+mypy+unit via `make test-unit`), on-demand
`alkyone.yml` (guarded), `cd.yml` deploy on push to main. Gaps:

1. **CI image bootstrap chicken-and-egg** — `ci-env:latest` must exist before container jobs run;
   on fresh clone CI fails. FIX: self-heal (build when missing).
2. **No prod `Dockerfile` build in CI** — compose/prod image never validated on PR. FIX: add
   `build-prod-image` job (build on PR; push on main).
3. **`cd.yml` not gated on CI results** — failing CI can still deploy. FIX: `needs: ci` + preflight
   smoke step before systemd restart.
4. **`alkyone.yml` vault guard is a no-op** — reads `HF_DATASET_ID`, job sets `HF_DATASET_ID_TEST`.
   FIX: align env names.
5. **Dead/legacy workflow clutter** — `data_collector.yml` (missing `./collector`), 4 `train_*.yml`
   with schedules commented out, legacy `ml-*` stack. FIX: delete or revive; stop re-using
   `DATABASE_URL` secrets across workloads.
6. **Config drift** — mypy strict in atlas/maia but not alkyone; ruff selects differ root vs mcp;
   pre-commit commit-stage omits mcp but pre-push includes it. FIX: unify.
7. **Compose secret name mismatch** — `YOUTUBE_API_KEYS` (compose/legacy) vs
   `YOUTUBE_API_KEY_POOL_JSON` (code). FIX: align.

## Execution order (per user: c FIRST, then b, then a orchestrator)

1. (c) Test-suite normalization: delete ~14 duplicate move-candidates; migrate only ~6 net-new
   business tests into maia unit suites; FIX the 3 placeholder/permissive asserts; keep alkyone
   integration/pipeline-only. [OPEN QUESTION awaiting user: delete-dups vs relocate-all.]
2. (b) Add tests for velocity SQL, watchlist boundaries, scribe loader/JSON, heartbeat staleness,
   tracker_flow idle/cap/notify — the untested core. Feature-first.
3. (a) Orchestrator tests — first write orchestrator-contract.md, then tests. Add compression guard.
4. (a) Safe deletions (dead code, legacy flows, unused settings) — verify against new tests.
5. (a) Slow-code fixes (N+1 collapse, blocking vault calls, dead audio path decision).
6. (b) Schema rewrite + migration on branch. DO NOT touch live DB without approval.
7. (c) Docs fixes.
8. (d) CI/CD hardening.
9. Final: full `make test-unit` + ruff + mypy; summarize; leave branch for review.

## Hard constraints

- Live DB read-only during all of this.
- Never print/commit secrets (real Mistral key in `SUMMARY.md` must be scrubbed, not committed).
- Per-change lands as its own commit on a feature branch; nothing auto-merged.
- alkyone integration never runs against prod (guard enforced).
