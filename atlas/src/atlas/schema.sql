CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS channels (
    id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    country VARCHAR(10),
    custom_url VARCHAR(100),
    created_at TIMESTAMPTZ,
    is_verified BOOLEAN DEFAULT FALSE,
    last_scraped_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channel_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id VARCHAR(50) REFERENCES channels(id) ON DELETE CASCADE,
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    old_title VARCHAR(255),
    new_title VARCHAR(255),
    event_type VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_stats_log (
    channel_id VARCHAR(50) REFERENCES channels(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    view_count BIGINT,
    subscriber_count BIGINT,
    video_count INTEGER,
    PRIMARY KEY (channel_id, timestamp)
);

CREATE TABLE IF NOT EXISTS videos (
    id VARCHAR(20) PRIMARY KEY,
    channel_id VARCHAR(50) REFERENCES channels(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    duration INTEGER,
    tags TEXT[],
    category_id VARCHAR(10),
    default_language VARCHAR(10),
    wiki_topics TEXT[],
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'PENDING',
    has_transcript BOOLEAN DEFAULT FALSE,
    has_visuals BOOLEAN DEFAULT FALSE,
    -- Extracted audio has been stored to the vault by the streamer producer at
    -- `audio/{id}.opus`. Consumers (the singer) use this flag to know the audio
    -- file is available without re-fetching it from YouTube.
    has_audio BOOLEAN DEFAULT FALSE,
    -- The YouTube source media has been fetched by the streamer (network pull)
    -- and stored to the vault as a raw artifact at `raw_uri`. The singer
    -- consumer later extracts the speech track locally (no YouTube rate limit)
    -- and flips `has_audio`. Decoupling the network fetch from the local
    -- extraction lets the egress-IP-flagged VPS avoid repeated YouTube pulls.
    fetched BOOLEAN DEFAULT FALSE,
    -- Vault path of the raw fetched artifact (e.g. `raw/{id}.webm`).
    raw_uri VARCHAR(255),
    -- Timestamp the raw artifact was stored via mark_fetched. Drives the raw
    -- TTL reclamation window so the muralist (clip consumer) has a bounded
    -- chance to derive from it before it is reclaimed.
    raw_stored_at TIMESTAMPTZ,
    -- The full source video has been archived to the vault by the muralist
    -- consumer at `videos/{id}.mp4`. Marked once the full clip is stored.
    has_video BOOLEAN DEFAULT FALSE,
    -- Staging for the janitor-owned vault write (Option A): the scribe stages
    -- the transcript (in `transcripts`) + audio bytes here; the janitor flushes
    -- them to the vault in batched commits. `vault_write_pending` is the work
    -- queue; `audio_pending` holds the extracted opus until it is persisted.
    vault_write_pending BOOLEAN DEFAULT FALSE,
    audio_pending BYTEA
);

-- Idempotent migration for existing deployments: the `has_audio` and
-- `has_video` columns were added after the `videos` table first shipped.
-- `ADD COLUMN IF NOT EXISTS` is a no-op on a fresh database where the
-- CREATE TABLE above already has them.
ALTER TABLE videos ADD COLUMN IF NOT EXISTS has_audio BOOLEAN DEFAULT FALSE;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS has_video BOOLEAN DEFAULT FALSE;
-- `fetched` / `raw_uri` added when the streamer/singer pipeline was split into
-- a network-fetch (streamer) + local-extract (singer) pair.
ALTER TABLE videos ADD COLUMN IF NOT EXISTS fetched BOOLEAN DEFAULT FALSE;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS raw_uri VARCHAR(255);
-- `raw_stored_at` added to bound raw-artifact retention (muralist TTL window).
ALTER TABLE videos ADD COLUMN IF NOT EXISTS raw_stored_at TIMESTAMPTZ;
-- `has_captions` / `captions_uri` were added when the streamer fetched captions
-- alongside the raw media. Caption ownership has since moved entirely to the
-- Scribe (single `timedtext` throttle surface, with an audio-STT fallback), so
-- the streamer no longer stores captions and these columns are dead. Dropped.
ALTER TABLE videos DROP COLUMN IF EXISTS has_captions;
ALTER TABLE videos DROP COLUMN IF EXISTS captions_uri;

-- Cheap lookup of videos awaiting a vault flush (janitor work queue).
CREATE INDEX IF NOT EXISTS idx_videos_vault_pending
    ON videos (id) WHERE has_transcript AND vault_write_pending;

COMMENT ON COLUMN videos.status IS
'Lifecycle state machine: PENDING → PROCESSING → PROCESSED → ARCHIVED | FAILED';

CREATE TABLE IF NOT EXISTS video_stats_log (
    video_id VARCHAR(20) REFERENCES videos(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    views BIGINT,
    likes BIGINT,
    comment_count BIGINT,
    PRIMARY KEY (video_id, timestamp)
);

CREATE TABLE IF NOT EXISTS system_events (
    id UUID DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(50),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
);

CREATE TABLE IF NOT EXISTS search_queue (
    id SERIAL PRIMARY KEY,
    query_term TEXT UNIQUE NOT NULL,
    priority INTEGER DEFAULT 0,
    mention_count INTEGER DEFAULT 0,
    next_page_token TEXT,
    last_searched_at TIMESTAMPTZ,
    result_count_total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    -- When the term entered the queue; drives time-decay scoring (Phase 2).
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id VARCHAR(20) PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    language VARCHAR(10) DEFAULT 'en',
    -- vault_uri is NULL while the transcript is staged locally (Option A);
    -- the janitor fills it in once it flushes the content to the vault.
    vault_uri TEXT,
    -- Staged transcript content (Option A): the scribe writes the segments here
    -- so the janitor can flush them to the vault; NULLed after a successful
    -- vault write. Keeps the persistence path decoupled from extraction.
    content JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchlist (
    video_id VARCHAR(20) PRIMARY KEY,
    tracking_tier VARCHAR(20) DEFAULT 'HOURLY' CHECK (tracking_tier IN ('HOURLY', 'DAILY', 'WEEKLY')),
    last_tracked_at TIMESTAMPTZ,
    next_track_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE watchlist IS
'Adaptive Scheduling: Persistent tracking schedule independent of video retention.
EXCLUDED from Janitor cleanup to enable long-term metrics collection.';

SELECT create_hypertable('channel_stats_log', 'timestamp',
    if_not_exists => TRUE, migrate_data => TRUE);
SELECT create_hypertable('video_stats_log', 'timestamp',
    if_not_exists => TRUE, migrate_data => TRUE);
SELECT create_hypertable('system_events', 'created_at',
    if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS idx_channel_scrape ON channels(last_scraped_at ASC);
CREATE INDEX IF NOT EXISTS idx_channel_history_channel ON channel_history(channel_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_video_publish ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_video_tags ON videos USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_video_category ON videos(category_id);
CREATE INDEX IF NOT EXISTS idx_video_tracker_staleness ON videos(last_updated_at ASC NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_video_status ON videos(status, discovered_at);
CREATE INDEX IF NOT EXISTS idx_video_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_search_queue_fetch ON search_queue(priority DESC, mention_count DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_next_track ON watchlist(next_track_at ASC);
CREATE INDEX IF NOT EXISTS idx_watchlist_tier ON watchlist(tracking_tier, next_track_at ASC);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_entity ON system_events(entity_id, created_at DESC);

-- Partial indexes for the hottest claim/sweep paths. These cover only the
-- rows the agent fleet actually scans, keeping the index small and the
-- planner's selectivity high (PostgreSQL Engineering: partial indexes).
CREATE INDEX IF NOT EXISTS idx_video_scribe_claim ON videos(discovered_at ASC)
    WHERE status IN ('PENDING', 'PROCESSING') AND has_transcript = FALSE;
CREATE INDEX IF NOT EXISTS idx_video_painter_claim ON videos(discovered_at ASC)
    WHERE status IN ('PENDING', 'PROCESSING') AND has_visuals = FALSE;
CREATE INDEX IF NOT EXISTS idx_video_streamer_claim ON videos(discovered_at ASC)
    WHERE status IN ('PENDING', 'PROCESSING') AND fetched = FALSE;
CREATE INDEX IF NOT EXISTS idx_video_singer_claim ON videos(discovered_at ASC)
    WHERE status IN ('PENDING', 'PROCESSING') AND fetched = TRUE AND has_audio = FALSE;
CREATE INDEX IF NOT EXISTS idx_video_muralist_claim ON videos(discovered_at ASC)
    WHERE status IN ('PENDING', 'PROCESSING') AND has_video = FALSE;
CREATE INDEX IF NOT EXISTS idx_video_sweep ON videos(last_updated_at ASC)
    WHERE status = 'PROCESSED';

-- ===========================================================================
-- Storage Limits & Retention Policies
-- ===========================================================================
-- This VPS has ~41 GB free. Each row in the stats logs is ~60 bytes.
-- Without retention, the pipeline fills the disk in weeks. The settings
-- below cap growth at ~4 GB total for the time-series tables.

-- Per-table row caps (enforced by Janitor sweep):
--   channel_stats_log: 500 000 rows  ≈ 30 MB
--   video_stats_log:   2 000 000 rows ≈ 120 MB
--   system_events:     100 000 rows   ≈ 50 MB
--   transcripts:       metadata only (payload in HF vault)

COMMENT ON TABLE channel_stats_log IS
'Time-series channel metrics. Retention: 500K rows (~30 MB). Janitor sweeps oldest first.';
COMMENT ON TABLE video_stats_log IS
'Time-series video metrics. Retention: 2M rows (~120 MB). Janitor sweeps oldest first.';
COMMENT ON TABLE system_events IS
'System event log. Retention: 100K rows (~50 MB). Janitor sweeps oldest first.';
COMMENT ON TABLE search_queue IS
'Search term queue. Janitor removes inactive/resolved terms.';
COMMENT ON TABLE transcripts IS
'Transcript metadata vault pointers. Payload lives in HF dataset, not Postgres.';

-- When TimescaleDB is available, uncomment:
-- SELECT add_retention_policy('channel_stats_log', INTERVAL '90 days');
-- SELECT add_retention_policy('video_stats_log', INTERVAL '90 days');
-- SELECT add_retention_policy('system_events', INTERVAL '30 days');

-- ──────────────────────────────────────────────────────────────────────────────
--  Storage-enforcement helper: deletes oldest rows from a table once it
--  exceeds *max_rows*.  Called by the Janitor's quota‑enforcement phase.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION enforce_table_row_limit(
    tbl REGCLASS,
    max_rows BIGINT
) RETURNS INT
    LANGUAGE plpgsql
    SECURITY INVOKER
AS $$
DECLARE
    current_count BIGINT;
    deleted_count INT := 0;
    pk_col        TEXT;
    quote_tbl     TEXT;
BEGIN
    EXECUTE 'SELECT COUNT(*) FROM ' || tbl::TEXT INTO current_count;
    IF current_count <= max_rows THEN
        RETURN 0;
    END IF;

    -- Derive a safe quoted name & pick the first PK column
    quote_tbl := tbl::TEXT;
    SELECT a.attname INTO pk_col
      FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid
                                     AND a.attnum = ANY(i.indkey)
     WHERE i.indrelid = tbl AND i.indisprimary
     LIMIT 1;

    IF pk_col IS NULL THEN
        RAISE WARNING 'enforce_table_row_limit: no PK found on % — skipping', quote_tbl;
        RETURN 0;
    END IF;

    EXECUTE format(
        'WITH to_delete AS (
            SELECT %I FROM %s ORDER BY %I ASC OFFSET %s
         )
         DELETE FROM %s WHERE %I IN (TABLE to_delete)',
        pk_col, quote_tbl, pk_col, max_rows, quote_tbl, pk_col
    );
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;

COMMENT ON FUNCTION enforce_table_row_limit(REGCLASS, BIGINT) IS
'Delete oldest rows once table exceeds max_rows. Returns count of rows removed.';

-- ===========================================================================
-- P1b migration: explicit per-step state (fan-out / fan-in join barrier)
-- Each artifact in the `raw -> {singer, painter, muralist}` fan-out and the
-- downstream `scribe` gets its OWN phase column instead of a conjunction of
-- booleans. `pipeline_phase` is a *derived* frontier (a video's progress
-- readable as a state, not inferred from booleans) for ops/monitoring only —
-- it does NOT drive claim selection, so the parallel topology is preserved.
-- The legacy booleans (fetched/has_audio/...) stay as a transitional seam kept
-- in sync by `sync_step_phases`; they are removed in P3
-- (docs/agent-consolidation-proposal.md). All statements are idempotent so
-- `provision_schema` can re-apply them on every agent startup.
-- ===========================================================================

DO $$ BEGIN
    CREATE TYPE step_phase AS ENUM ('PENDING', 'PROCESSING', 'DONE', 'FAILED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE videos ADD COLUMN IF NOT EXISTS raw_phase step_phase DEFAULT 'PENDING';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS audio_phase step_phase DEFAULT 'PENDING';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS visuals_phase step_phase DEFAULT 'PENDING';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS transcript_phase step_phase DEFAULT 'PENDING';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS clip_phase step_phase DEFAULT 'PENDING';

-- Frontier = the earliest step still not DONE (pipeline order). IMMUTABLE so it
-- can back a generated column.
CREATE OR REPLACE FUNCTION pipeline_frontier(
    raw step_phase, audio step_phase, visuals step_phase,
    transcript step_phase, clip step_phase
) RETURNS VARCHAR LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN raw <> 'DONE' THEN 'RAW'
        WHEN audio <> 'DONE' THEN 'AUDIO'
        WHEN visuals <> 'DONE' THEN 'VISUALS'
        WHEN transcript <> 'DONE' THEN 'TRANSCRIPT'
        WHEN clip <> 'DONE' THEN 'CLIP'
        ELSE 'DONE'
    END;
$$;

ALTER TABLE videos ADD COLUMN IF NOT EXISTS pipeline_phase VARCHAR GENERATED ALWAYS AS (
    pipeline_frontier(raw_phase, audio_phase, visuals_phase, transcript_phase, clip_phase)
) STORED;

-- Backfill phase columns from the legacy booleans — only rows where a boolean
-- is TRUE but its phase is not yet DONE (i.e. pre-migration data). Genuinely
-- pending rows (boolean FALSE, phase PENDING) are intentionally left alone so
-- this is a no-op after the first run.
UPDATE videos SET
    raw_phase        = CASE WHEN fetched        THEN 'DONE' ELSE raw_phase END,
    audio_phase      = CASE WHEN has_audio       THEN 'DONE' ELSE audio_phase END,
    visuals_phase    = CASE WHEN has_visuals     THEN 'DONE' ELSE visuals_phase END,
    transcript_phase = CASE WHEN has_transcript  THEN 'DONE' ELSE transcript_phase END,
    clip_phase       = CASE WHEN has_video       THEN 'DONE' ELSE clip_phase END
WHERE (fetched AND raw_phase <> 'DONE')
   OR (has_audio AND audio_phase <> 'DONE')
   OR (has_visuals AND visuals_phase <> 'DONE')
   OR (has_transcript AND transcript_phase <> 'DONE')
   OR (has_video AND clip_phase <> 'DONE');

-- Bidirectional sync: keep booleans and phase columns consistent regardless of
-- which code path writes which. Old agents that only touch booleans still keep
-- phases correct; new code that drives phases keeps booleans correct.
CREATE OR REPLACE FUNCTION sync_step_phases() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.fetched IS DISTINCT FROM OLD.fetched THEN
        NEW.raw_phase := CASE WHEN NEW.fetched THEN 'DONE'::step_phase ELSE 'PENDING'::step_phase END;
    ELSIF NEW.raw_phase IS DISTINCT FROM OLD.raw_phase THEN
        NEW.fetched := (NEW.raw_phase = 'DONE');
    END IF;
    IF NEW.has_audio IS DISTINCT FROM OLD.has_audio THEN
        NEW.audio_phase := CASE WHEN NEW.has_audio THEN 'DONE'::step_phase ELSE 'PENDING'::step_phase END;
    ELSIF NEW.audio_phase IS DISTINCT FROM OLD.audio_phase THEN
        NEW.has_audio := (NEW.audio_phase = 'DONE');
    END IF;
    IF NEW.has_visuals IS DISTINCT FROM OLD.has_visuals THEN
        NEW.visuals_phase := CASE WHEN NEW.has_visuals THEN 'DONE'::step_phase ELSE 'PENDING'::step_phase END;
    ELSIF NEW.visuals_phase IS DISTINCT FROM OLD.visuals_phase THEN
        NEW.has_visuals := (NEW.visuals_phase = 'DONE');
    END IF;
    IF NEW.has_transcript IS DISTINCT FROM OLD.has_transcript THEN
        NEW.transcript_phase := CASE WHEN NEW.has_transcript THEN 'DONE'::step_phase ELSE 'PENDING'::step_phase END;
    ELSIF NEW.transcript_phase IS DISTINCT FROM OLD.transcript_phase THEN
        NEW.has_transcript := (NEW.transcript_phase = 'DONE');
    END IF;
    IF NEW.has_video IS DISTINCT FROM OLD.has_video THEN
        NEW.clip_phase := CASE WHEN NEW.has_video THEN 'DONE'::step_phase ELSE 'PENDING'::step_phase END;
    ELSIF NEW.clip_phase IS DISTINCT FROM OLD.clip_phase THEN
        NEW.has_video := (NEW.clip_phase = 'DONE');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sync_step_phases_trigger ON videos;
CREATE TRIGGER sync_step_phases_trigger
    BEFORE INSERT OR UPDATE ON videos
    FOR EACH ROW EXECUTE FUNCTION sync_step_phases();
