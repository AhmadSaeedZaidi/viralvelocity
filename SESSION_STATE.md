# Pleiades Orchestration — Session State (hy3-work)

## What was actually broken (root causes found this session)
The "vicious cycle" was NOT the worker websocket (that was fixed earlier via
`PREFECT_WORKER_ENABLE_CANCELLATION=false` in `/etc/systemd/system/prefect-worker.service`).
The real blockers were credential/infra layer:

1. **11 of 24 YouTube API keys were DEAD (HTTP 403 revoked).** The KeyRing
   (`atlas/src/atlas/utils.py`) round-robined through ALL keys including dead ones
   and — worse — `_is_quota_error()` explicitly returned `False` for "http 403",
   so a dead key raised `QuotaExhaustedError` on the FIRST call instead of
   rotating. This killed hunter/archeologist/scribe/tracker instantly.
   FIX: purged 11 dead keys from `YOUTUBE_API_KEY_POOL_JSON` in `.env` (now 13 live).
   FIX: `_is_quota_error` now treats 401/403/429 as rotation triggers; added
   `KeyRing.mark_key_dead()` to blacklist 403 (revoked) keys permanently while
   429 (transient rate-limit) rotates without blacklisting.

2. **Key pool allocation starved archeology** (`atlas/src/atlas/key_pool.py`).
   Old `compute_sizes` gave archeology only 2-3 keys; when those hit 429 the ring
   fully blocked. FIX: archeology floor = max(configured, total//3); tracker floor
   capped at 6 (was 8). Cache rewritten to `data/pool_allocation.json`.

3. **4 orphaned "RUNNING" scribe zombie flow runs** (subprocesses died on worker
   restart but never got terminal state) occupied the scribe queue
   (concurrency_limit=1) → all new scribe runs stuck SCHEDULED forever.
   FIX: force-failed the 4 zombies via API. NOTE: janitor reaper should catch
   RUNNING>15min but didn't catch these — revisit if it recurs.

4. **`pysocks` was missing** → scribe's `socks5h://127.0.0.1:1090` proxy egress
   was unusable by yt-dlp. FIX: `pip install pysocks`.

## Current healthy state (verified)
- Worker: no WS crash-loop (0 since 08:06 restart). Runs: singer, painter, tracker,
  hunter, archeologist all executing.
- transcripts 15m: 1-2 (scribe working again). raw 15m: 21 (streamer working).
- tracker COMPLETED with Fetched:50 Updated:49 (was HTTP 403 before).
- hunter COMPLETED (enriched channels).
- No zombie runs (TOTAL RUNNING=2, expected).

## Key facts
- Server (micro): 10.0.0.22, Postgres. Worker: executor 10.0.0.6.
- Proxy service = `youtube-proxy.service` (SSH tunnel to micro, port 1090).
  Scribe uses proxy-only egress; audio agents use direct.
- Scribe uses yt-dlp native captions (NOT YouTube Data API key), so it's
  independent of the key pool. Caption extraction (`StealthVideoStreamer.extract_captions`)
  works with `--impersonate Chrome-131 --js-runtimes deno:/usr/local/bin/deno`.
- Janitor runs every 15min; `reap_zombie_runs_task` crashes RUNNING>15min.

## TODO (not yet done)
- Clean up leaked /tmp dirs (singer-audio-*, scribe-audio-*, painter-raw-*).
- Verify archeology fully recovers once its 429'd keys' daily quota resets
  (archeology was in a 429 storm at session end but ROTATING, not crashing).
- Consider whether janitor reaper reliably catches orphaned runs on worker restart.
