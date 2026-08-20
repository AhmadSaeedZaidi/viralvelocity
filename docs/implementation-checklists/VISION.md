# Product Vision — Pleiades (canonical, survives context compression)

WRITE-ONCE reference. If context is compressed, re-read this before continuing test work.
Do not delete. Last updated: session on Aug 03 2026.

## North star (from the maintainer)
Repository's purpose: **collect data from YouTube.**

### Core pipeline (implemented)
1. **Discovery / hunt** — find *interesting* videos across **varying topics** (snowball/filtering).
2. **Asset harvest** per video:
   - **key frames** (painter / `visuals`),
   - **audio** (streamer+singer / `audio`),
   - **transcripts** (scribe / `transcript`).
3. **Enrichment — metadata tracking** — views, likes, comments, **growth** over time (tracker,
   `video_stats_log` time series; velocity → adaptive watchlist scheduling).

### Knowledge graph (NOT NOW — explicit TODO)
Idea: enrich with a **knowledge graph built from the `topicDetails` resource part** of the
YouTube Data v3 API (video topics / entities + relations). This is a **TODO / stretch**, NOT part
of current work. Do not build it now; do not let scaffolding for it block the current tasks.

## Architectural context
- **orchestrator** (`maia/src/maia/orchestrator.py`) was added LATER, when the system moved to a
  **dual-server** layout to divide load between a **micro** and a **2-core bigger** box.
  → graph independent of probe is fine; orchestrator is a post-hoc scheduler, not core vision.
- Primary DB is live Postgres (`127.0.0.1:5432/pleiades`) — READ-ONLY during all cleanup/migration
  work; migrations need explicit approval + backup.
- Knowledge of existing components: hunter, streamer/singer, painter, scribe, tracker, janitor,
  heartbeat, quality gates, key_pool, vault(egress), watchlist (adaptive), orchestrator.

## Work ordering (explicit maintainer instruction)
1. **(c)** the no-op / placeholder tests FIRST.
2. **(b)** velocity SQL tests (and untested adaptive-scheduling core) SECOND.
3. **(d)** orchestrator testing LATER (only after c & b); must be robust to context compression.
4. (a) code cleanup + DB 3NF + docs + CI/CD only after the above (pending sign-off).

## Verification bar (test-first)
Rather than refactor code then guess, each change should be anchored to a test that encodes the
product intent above. Treat suite as an oracle for the vision. Real-infra (alkyone) tests are
guarded and must never point at prod.