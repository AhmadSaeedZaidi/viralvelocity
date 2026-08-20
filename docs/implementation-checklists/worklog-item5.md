# Worklog — Item 5: Slow-code fixes

Dispatched to `perf-fixer` subagent. Subagent made the code changes but did not
write this worklog or a final reply (recurring reliability gap). I reviewed every
diff and added this record.

## Fixes applied (all verified against live schema, no schema changes)

### 1. N+1 SQL collapses
- `atlas/src/atlas/repositories/video/quality.py` — `quality_report()`: collapsed
  6 scalar `COUNT` queries into ONE aggregate `SELECT` with `COUNT(*) FILTER (...)`
  for the 6 metrics (total, total_with_duration, shorts_under_3m + 4 artifact
  coverage counters). Kept the separate GROUP BY queries for status/buckets/coverage
  shape. Leaves result keys unchanged.
- `atlas/src/atlas/repositories/video/tracking.py` — `metrics()`: collapsed 9
  scalar `COUNT` queries into ONE aggregate over `videos` (total, with_visuals,
  audios, ingested_1h, tracked_ever, tracked_1h, tracked_24h) using
  `FILTER` + param-bound cutoffs. Kept transcripts/video_stats_log counts and
  status/phase GROUP BYs separate. Result keys unchanged.
- `atlas/src/atlas/repositories/video/janitor.py` + `ingestion.py` — added
  `VideoIngestionMixin.get_latest_stats_batch(video_ids)` using `DISTINCT ON
  (video_id) ... ORDER BY video_id, timestamp DESC` to fetch latest stats for the
  whole archive batch in ONE query. `archive_video_batch` now preloads
  `latest_stats_map` once instead of `await get_latest_stats(video.id)` per video
  (the archive-loop N+1). Updated `test_janitor_vault_gate.py` to mock
  `get_latest_stats_batch`.

### 2. Dead audio data path (legacy `audio_pending` staging)
Removed consistently across every layer. Post-fix, `audio_pending` is referenced
only by `schema.sql:70-72` (the COLUMN still exists) and code comments. Column
removal is deferred to item 6 (3NF schema rewrite) — intentionally NOT changed here.
- `atlas/src/atlas/repositories/video/transcript.py` — `write_transcript()` no
  longer accepts `audio_bytes` or writes `audio_pending`; `claim_vault_pending_batch()`
  no longer SELECTs `v.audio_pending` nor clears it; `mark_vault_flushed()` no longer
  sets `audio_pending = NULL`.
- `maia/src/maia/janitor/flow.py` — `vault_flush_task()` no longer stages
  `audio/{id}.opus` bytes (singer writes audio straight to vault); `CHUNK` comment
  simplified; removed now-unused `import io`.
- Rationale: singer writes `audio/{id}.opus` directly to the vault, so the legacy
  staging/claim path was never consumed.

### 3. Dead method removal (carried from item 4, verified)
`VideoIngestionMixin.save()` removed (superseded by `ingest_video_metadata` /
`ingest_channel_snapshot`); protocol declaration dropped in `protocols.py`.

## KEPT with reason
- `get_latest_stats(video_id)` (singular) retained — still used by
  `channel.py` (per-channel stats) and declared in `protocols.py`. Only the video
  archive loop moved to the batch method.
- `audio_pending` column left in schema (item 6 scope), repo SQL untouched.

## Verification
- `maia/tests`: **164 passed**.
- `atlas/tests -k "not integration"`: **71 passed**.
- `ruff check maia/`: **5 → 3** (item-4 baseline). Remaining 3 all pre-existing
  style debt: `janitor/flow.py:179` I001 import-block sort, `:189`/`:250` UP017
  (`datetime.now(timezone.utc)` → `datetime.UTC`), `thresholds.py` E501, `tracker/flow.py`
  E501. NONE introduced by item 5.
- `ruff check atlas/`: **2** pre-existing E501 in `tracking.py`/`utils.py` — unchanged.
- No schema edits; live DB untouched.

## Audit note
The `audio_pending` dead column is now a confirmed item-6 (db-schema-architect)
target: drop `audio_pending BYTEA` from `atlas/src/atlas/schema.sql` and any
supporting table/constraint during the 3NF rewrite.