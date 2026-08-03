# HAND-OFF — start here (new session)

Author: prior session, 2026-08-03. Read this file + `docs/implementation-checklists/cleanup-plan.md`.
Working dir: `/home/ubuntu/code/pleiades`. Branch: `wip/adaptive-scheduling-and-mcp` (working tree clean,
in-flight WIP committed as `5122ad9`).

## What just happened (context so you don't rediscover it)
- A 4-part cleanup engagement was scoped at user request: (a) code cleanup, (b) DB -> 3NF,
  (c) docs, (d) CI/CD.
- FOUR read-only audits were completed (via subagents) and their findings persist in
  `docs/implementation-checklists/cleanup-plan.md`: code-quality, DB-3NF-gaps, docs+CI/CD, and
  a test-suite inventory/gap-analysis.
- The live DB (`pleiades` at 127.0.0.1:5432) is READ-ONLY for all of this work. Do NOT migrate it.
- User's product vision (drives ALL testing decisions):
  1. DISCOVER (hunter + search_queue + filtering/quality)
  2. CAPTURE (painter keyframes; streamer/singer audio; scribe transcripts)
  3. ENRICH (tracker + video_stats_log + watchlist adaptive-sched; views/likes/comments/growth)
  4. Knowledge graph via topicDetails (v3 API) -- TODO, OUT OF SCOPE now.

## Authoritative test-architecture policy (user-agreed) — do not violate
- alkyone = PIPELINE/INTEGRATION testing library ONLY (real infra; guarded vs prod).
- atlas/maia/mcp = PER-AGENT UNIT tests only (mocked collaborators).
- Both kinds are wanted; this split is the deciding rule.

## Agreed execution order (async from user: c first, then b, then a)
1. **(c)** Test-suite normalization (see below). [HAS ONE OPEN QUESTION.]
2. **(b)** Add unit tests for the untested core (velocity SQL, watchlist boundaries, scribe
   loader/JSON, heartbeat staleness, tracker_flow idle/cap/notify). Feature-first: write tests,
   then verify the code they cover.
3. **(a)** Orchestrator tests — FIRST create `orchestrator-contract.md`, then tests (see
   compression guards).
4-9. Remaining cleanup/schema/docs/CI-CD per `cleanup-plan.md §Execution order`.

## Task (c) — specifics (goal: align the test split)
Subagent findings (cited in cleanup-plan.md "session update" section):
- ~20 mocked-unit tests currently live in `alkyone/tests` and should not be there per policy.
  MOST (~14) are DUPLICATES of existing maia unit tests; ~6 are net-new valid business cases.
- FIX placeholders: `test_adaptive_scheduling.py:86` (assert True), `test_scribe.py:132`
  (assert True), archeologist real tests permissive `[200,403,429]`.
- KEEP the true integration tests (correctly gated by conftest.py:66-114 skip logic + guard.py).

**OPEN QUESTION the user must answer before you act on (c):**
Delete the ~14 duplicate move-candidates (recommended — keeps alkyone clean/integration-only) vs
relocate them into maia. Migrate only the ~6 net-new ones regardless. Ask the user; do not assume.

## Guards against context compression (user explicitly requested)
- Use subagents for heavy research/audits; never hold big analyses in your own window.
- Persist decisions + a checklist to a file under `docs/implementation-checklists/` BEFORE
  starting any task. Re-read that ledger at the start of every session.
- For orchestrator work specifically: create `orchestrator-contract.md` capturing dual-server
  division, `run_cycle` failure isolation, signal drain, jitter stagger — before writing tests.

## Commands / how to verify
- Tests: `cd <pkg> && poetry run pytest tests -q` or `make test-unit` at root (atlas+maia+mcp).
- Integration (alkyone): `make test-int` — runs guard first, hard-refuses prod. Needs real creds.
- Lint: `make lint` (ruff + mypy per package). Config drift between packages is a known (d) issue.
- Unit tests run with fake `pytest-env` secrets — no real DB/API needed.

## Reminders / gotchas
- Never print or commit the real Mistral API key found in `SUMMARY.md` (gitignored) — planned scrub.
- Live DB is read-only THIS session. Schema changes only on a branch for review.
- Multiple stale scratch docs exist (summary.md vs SUMMARY.md); dedup is part of (c) docs task.