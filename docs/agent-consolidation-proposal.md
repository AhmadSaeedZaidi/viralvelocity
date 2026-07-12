# Agent Consolidation & Test-Strategy Proposal

> Status: **PARTIALLY IMPLEMENTED.** P1a is shipped; P1b/P2/P3/P4 pending. CI/quality work (mypy 2.x, locked deps, alkyone unhooked from CI) done separately.
> Companion to `refactor_draft.md` (Atlas DAO → Repository). This doc extends that
> philosophy to the **maia agent layer** and resolves the **alkyone / 24-7-VPS** question.
> Author: opencode. Reviewed against code as of 2026-07-11.

## Implementation Status (2026-07-12)

| Phase | State | Notes |
|---|---|---|
| P1a | Done | `a4762b7`: claim gates + TTL reclamation + `raw_stored_at` + 13 tests; live DB migrated |
| P1b | Pending | `pipeline_phase` enum migration (deferred per plan) |
| P2  | Pending | `BaseBatchAgent` + remove legacy cruft |
| P3  | Pending | decompose the five oversized files |
| P4  | Partial | alkyone unhooked from CI (jobs + image) ✓; `architecture.md` → Repository pattern ✓. TODO: alkyone isolated test infra + prod-URL guard, maia unit-test DRY, `testing.md` VPS rewrite |

Adjacent CI/quality work this session (not in plan, but related):
- mypy 2.x migration + poetry-lock enforcement (lock is now authoritative in the CI image build).
- alkyone removed from the CI env image (`.dockerignore` excludes it as a separate build context).
- GitHub Actions bumped to Node-24-targeting majors (silences the runner deprecation warnings).

Open questions in §5 still await a decision: muralist cadence, P1b timing,
alkyone schedule, doc ownership.

---

## 0. Why this exists now

Three things converged:

1. **Code-quality audit complete.** ast-grep + ruff pass; event-loop-blocking bugs fixed;
   error tracebacks recovered; stylistic nits resolved. The *surface* is clean.
2. **Design-debt audit (this doc).** Beneath the surface, the maia agent layer still has the
   problems `refactor_draft.md` fixed in atlas: duplicated scaffolding, overloaded state flags,
   and a producer/consumer coordination bug that bites `streamer → scribe/painter/muralist`.
3. **Deployment reality changed.** The system now runs 24/7 on a single VPS (this machine),
   not ephemeral CI runners. `docs/testing.md` and `docs/architecture.md` still describe the
   CI-runner world (Neon per-PR branches, GitHub Actions, mocked deps). That is the root of
   "I'm not sure what to do with alkyone."

This proposal is deliberately **phased and low-risk**: every step is independently testable and
deployable, matching the incremental style of `refactor_draft.md`.

---

## 1. Findings (the audit)

### F1 — Producer/Consumer coordination bug (the streamer → scribe/painter/muralist issue)
The "queue" is the `videos` table; each agent polls a `claim_*_batch` SQL `WHERE` clause in
`atlas/src/atlas/repositories/video/state_machine.py`. Dependencies between streamer's `raw`
artifact and its three consumers are **not encoded in the claim queries** — they're implied by
overloaded boolean flags, which causes a real race:

- `reclaim_raw_if_complete` (`state_machine.py:166`) deletes `raw_uri` the moment
  **`has_audio AND has_visuals`** are both TRUE (`:178-191`).
- **Muralist consumes `raw`** (the source clip) to build `videos/{id}.mp4`, but
  `claim_muralist_batch` (`:102`) only checks `has_video = FALSE` — it does **not** require
  `raw_uri IS NOT NULL`.
- **Result:** if singer + painter finish before muralist runs, `raw` is already deleted →
  muralist claims the row with `raw_uri = NULL` and has no input. Muralist silently starves.
  This is exactly the failure you noticed. (Compounded by muralist being *manual-only* per
  `registry.py` — in production it may never run, so `raw` is reclaimed out from under it.)
- **Scribe has the same class of bug:** `claim_scribe_batch` (`:14`) only checks
  `has_transcript = FALSE` — it does **not** require `has_audio = TRUE` / `fetched = TRUE`.
  So scribe can claim a video before audio exists and either fail or re-hit YouTube (429 risk).

Root cause: **boolean flags encode both "artifact available" and "artifact consumed", with no
explicit DAG.** Reclamation is keyed on subtask completion, not "all dependent consumers done."

### F2 — No shared agent scaffolding (DRY violation)
- `agent.py` is a 21-line `Protocol` (`name` / `add_cli_args` / `run`). Every agent re-implements
  the same skeleton: `__init__`, `add_cli_args("--batch-size")`, `async def run`, cycle logging.
  Copied ×9 (`scribe/flow.py:178-252` is one example).
- Each flow repeats the `Semaphore` + `gather(return_exceptions=True)` + store pattern.
- **Legacy cruft:** `scribe/flow.py:216-244` still carries `process_transcript` task wrapper,
  `run_scribe_cycle` flow, `main()` + `__main__` — dead backward-compat code duplicated across
  agents.

### F3 — Oversized files mixing concerns
Five files > 420 lines that each mix orchestration + domain + I/O:

| File | Lines | Mixes |
|---|---|---|
| `maia/media/streamer.py` | 500 | yt-dlp network, rate-limit, cookie/PoToken, format selection, vault I/O |
| `maia/painter/flow.py` | 499 | frame extraction + vault range requests + DB updates + quality gating |
| `maia/janitor/flow.py` | 487 | archival/retention orchestration + vault + DB |
| `maia/quality.py` | 455 | scoring heuristics shared by hunter/archeologist/janitor |
| `maia/hunter/flow.py` | 421 | discovery + quality gate + snowball + DB |

These shrink substantially if the batch-loop and repo-transaction boilerplate are extracted.

### F4 — Documentation is stale (and therefore a maintainability hazard)
- `docs/architecture.md` still recommends the **DAO pattern** (`dao = MaiaDAO()`) the
  `refactor_draft.md` already replaced with Repositories, and describes agent roles that no
  longer match the code (Painter "enriches metadata with external APIs"; Scribe "extracts
  features for ML"). It also describes Streamer as setting `has_audio` directly, but `singer`
  does. The doc actively misleads a new reader.
- `docs/testing.md` assumes **ephemeral Neon branches per PR + GitHub Actions** — infrastructure
  that no longer exists on a 24/7 VPS.

### F5 — alkyone's purpose is undefined on a 24/7 VPS
alkyone was "live integration tests + smoke tests", run in CI against isolated Neon branches.
On a VPS there is no CI and no per-PR isolation. If run as-written, alkyone's live tests hit the
**same production Postgres, YouTube quota, and HuggingFace vault** the 24/7 agents use — polluting
real data and burning quota. That ambiguity is what makes alkyone feel orphaned.

---

## 2. Principles (carry over from `refactor_draft.md`)

1. **One class per responsibility.** Extend it from atlas Repositories to maia flows.
2. **Explicit dependency graph.** A video's progress should be readable as a state, not inferred
   from a conjunction of booleans.
3. **Injectable dependencies for testing.** `refactor_draft.md` already wants the DB pool
   constructor-injectable; the same goes for the vault and the YouTube client, so unit tests
   need **zero** real infrastructure (matching its "mock the Repository / inject the pool" goal).
4. **DRY via focused interfaces**, not copy-paste.

---

## 3. Proposal

### P1 — Fix producer/consumer coordination (do the minimal correct fix first)
**Phase 1a (targeted, this sprint):**
- `claim_scribe_batch` → add `AND has_audio = TRUE` (scribe consumes audio; singer produces it).
- `claim_muralist_batch` → add `AND raw_uri IS NOT NULL` (safety gate; no input, no claim).
- `reclaim_raw_if_complete` → only reclaim when
  `has_audio AND has_visuals AND (has_video OR raw_age > raw_ttl)`.
  Add a `raw_ttl` setting (default ~48h) and a `raw_stored_at timestamptz` column set in
  `mark_fetched`. This keeps disk bounded when muralist is disabled, while letting a manual
  muralist run within the TTL window.

**Phase 1b (later, schema migration):** replace the overloaded boolean flags with an explicit
`pipeline_phase` enum (`DISCOVERED → FETCHED → AUDIO_DONE → VISUALS_DONE → TRANSCRIPT_DONE →
CLIP_DONE → PROCESSED`). Each consumer claims *its* phase; `raw` reclamation becomes a single
transition at `CLIP_DONE`. This removes the entire class of "flag says done but consumer hasn't
run" bugs. **Recommended to defer** until P2/P3 land, since it's a migration, not a bug fix.

### P2 — `BaseBatchAgent` to kill the ×9 duplication
Introduce a small base in `maia/agent.py` (or `maia/base.py`):

```python
class BaseBatchAgent(Protocol):
    name: str
    default_batch_size: int = 5
    async def claim_batch(self, n: int) -> list[Video]: ...
    async def process_one(self, video: Video) -> None: ...
    # shared: run() loops claim→semaphore+gather→store, cycle logging, return stats
```

Each concrete agent becomes ~30-60 lines: declare `name`, `claim_batch`, `process_one`.
Remove the legacy `process_transcript` / `run_scribe_cycle` / `main` / `__main__` cruft
(`scribe/flow.py:216-244` and siblings). `registry.py` + `__main__.py` dispatch unchanged.

### P3 — Decompose the five oversized files
Extract, don't rewrite:
- `media/streamer.py` (500): split **network/orchestration** (`streamer/flow.py`) from the
  **yt-dlp engine** (`media/streamer.py` stays the engine, shrinks).
- `quality.py` (455): move heuristics into a `quality/` package consumed by hunter/archeologist/
  janitor; each consumer imports only what it needs (mirrors the per-entity Repository idea).
- `painter/flow.py` (499) / `janitor/flow.py` (487) / `hunter/flow.py` (421): peel out
  vault-I/O and DB-transaction helpers into shared modules so the flow file reads as *policy*,
  not *plumbing*.

### P4 — Test strategy: keep the layering, fix the VPS gap
**Decision: do NOT move maia's 106 unit tests into alkyone.** That contradicts alkyone's own
charter (integration-only) and `refactor_draft.md`'s "mock the Repository / inject the pool"
unit-test philosophy. Instead:

- **`maia/tests/` (106 mocked unit tests):** keep in-repo, DRY them (shared fixtures, remove
  duplicate legacy tests), align mocks to the Repository imports (per `refactor_draft.md` Table).
  No move.
- **alkyone (live integration + smoke):** keep as the integration home, but **mandate isolated
  test infrastructure** so it's safe on a VPS:
  - alkyone must target a **separate test Postgres + separate test HF vault**, never the
    production `DATABASE_URL` / `HF_DATASET_ID` the agents use. (The alkyone README already
    half-specifies a `pleiades-test-vault`; make it required, not optional.)
  - Add a guard: `make test-int` refuses to run if `DATABASE_URL` points at the production DB.
  - On a VPS with no CI, alkyone runs **on-demand / manually** (and optionally a throttled
    scheduled job), never per-commit. It must **not** consume production YouTube quota
    (use a fixed small fixture set or mock the API) and must **not** write to the production
    vault.
  - **"Full rewrite" = rewrite alkyone's integration suite properly** (real isolation, correct
    markers, no contradiction with its README) + consolidate maia's unit tests in place. This
    resolves the F5 ambiguity without relocating 106 tests.
- **Docs:** rewrite `docs/testing.md` for the VPS model; refresh `docs/architecture.md` to the
  Repository pattern + correct agent roles + the real producer/consumer flow (P1).

---

## 4. Sequencing (each phase independently shippable)

| Phase | Work | Risk | Validated by |
|---|---|---|---|
| 0 | Doc refresh plan + alkyone isolation guard + test-DB provisioning | low | docs build, guard rejects prod URL |
| 1a | P1 minimal fix (claim gates + TTL reclamation) | low | existing + new unit tests on `state_machine` |
| 1b | P1 phase-enum migration | med | migration + integration test |
| 2 | P2 `BaseBatchAgent` + remove legacy cruft | med | 106 maia unit tests still green |
| 3 | P3 file decomposition | med | unit + integration green; smaller files |
| 4 | P4 alkyone rewrite + maia unit-test DRY + doc refresh | low-med | `make test` + `make test-int` (isolated) |

---

## 5. Open questions for you

1. **Muralist model:** is muralist meant to stay manual-only (→ TTL reclamation in P1a), or
   should it become a scheduled consumer that runs *before* `raw` reclamation? This decides
   whether P1a needs the TTL branch at all.
2. **P1b scope:** approve the `pipeline_phase` enum migration now, or strictly defer until P2/P3?
3. **alkyone cadence:** on-demand only, or a throttled scheduled job (e.g. nightly, against the
   isolated test DB)?
4. **Doc ownership:** may I update `docs/architecture.md` + `docs/testing.md` as part of Phase 0/4?
   (They currently mislead readers — see F4.)

---

## 6. References & Verified Best Practices

The following external references were verified (2026-07-11) to ground the
proposals in established practice (see the "upgrade" working session):

- **DRY via ABC + Template Method (P2 `BaseBatchAgent`)** — Python `abc`
  supports abstract methods *with* implementations (callable via `super()`),
  and a class with unimplemented abstract methods cannot be instantiated.
  Exactly the `BaseBatchAgent` shape (shared `run()` + abstract
  `claim_batch`/`process_one`).
  → https://docs.python.org/3/library/abc.html
- **Producer/Consumer coordination (P1)** — Transactional Outbox
  (microservices.io): the `videos` table *is* an outbox; consumers must be
  idempotent because relays can re-deliver. Validates the explicit-phase model
  and that the raw-reclamation race is fundamentally an *ordering* problem.
  → https://microservices.io/patterns/data/transactional-outbox.html
  (Related: Saga — each transition is an event.)
  → https://microservices.io/patterns/data/saga.html
- **Incremental modernization (P1→P4 sequencing)** — Strangler Fig (Fowler):
  modernize via *seams* + *transitional architecture* that later goes away;
  small independently-shippable parts. Justifies keeping legacy cruft behind a
  seam during P2, then deleting it.
  → https://martinfowler.com/bliki/StranglerFigApplication.html
- **Test strategy / alkyone isolation (P4)** — Google Test Sizes (Small/Medium/
  Large) maps to "maia `tests/` = unit, alkyone = integration against isolated
  infra", and mandates high test isolation (no cross-test data leakage).
  → https://testing.googleblog.com/2010/12/test-sizes.html
  pytest fixtures: `factories as fixtures`, `monkeypatch` (inject mock
  Repository / DB pool — exactly P4's "mock the Repository / inject the pool"),
  `scope`, and safe teardowns for hermetic cleanup.
  → https://docs.pytest.org/en/stable/how-to/fixtures.html
- **Config isolation (P4)** — Twelve-Factor: store config (DB handle, vault id,
  credentials) in env vars, never in code. Supports the isolated
  `DATABASE_URL`/`HF_DATASET_ID` + the prod-URL guard.
  → https://12factor.net/config

### Gaps the research surfaced (candidates for a later addendum)
1. **Idempotent consumers** (outbox lesson): add an explicit processed-marker
   so a manual muralist rerun cannot double-write — strengthens P1a.
2. **P1b is more strongly justified** than implied: the race is a missing
   ordered state machine, so the `pipeline_phase` enum is the root-cause fix,
   not just "nice to have".
3. **Transitional seams**: per Strangler Fig, P2/P3 should keep the old flow
   callable behind a seam until the new base class is proven, then remove it.
