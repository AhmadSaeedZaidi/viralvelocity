CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS channels (
    id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    country VARCHAR(10),
    custom_url VARCHAR(100),
    created_at TIMESTAMP,
    is_verified BOOLEAN DEFAULT FALSE,
    last_scraped_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channel_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id VARCHAR(50) REFERENCES channels(id) ON DELETE CASCADE,
    changed_at TIMESTAMP DEFAULT NOW(),
    old_title VARCHAR(255),
    new_title VARCHAR(255),
    event_type VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_stats_log (
    channel_id VARCHAR(50) REFERENCES channels(id) ON DELETE CASCADE,
    timestamp TIMESTAMP DEFAULT NOW(),
    view_count BIGINT,
    subscriber_count BIGINT,
    video_count INTEGER,
    PRIMARY KEY (channel_id, timestamp)
);

CREATE TABLE IF NOT EXISTS videos (
    id VARCHAR(20) PRIMARY KEY,
    channel_id VARCHAR(50) REFERENCES channels(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    published_at TIMESTAMP,
    duration INTEGER,
    tags TEXT[],
    category_id VARCHAR(10),
    default_language VARCHAR(10),
    wiki_topics TEXT[],
    discovered_at TIMESTAMP DEFAULT NOW(),
    last_updated_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'PENDING',
    has_transcript BOOLEAN DEFAULT FALSE,
    has_visuals BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS video_stats_log (
    video_id VARCHAR(20) REFERENCES videos(id) ON DELETE CASCADE,
    timestamp TIMESTAMP DEFAULT NOW(),
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
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
);

CREATE TABLE IF NOT EXISTS search_queue (
    id SERIAL PRIMARY KEY,
    query_term TEXT UNIQUE NOT NULL,
    priority INTEGER DEFAULT 0,
    mention_count INTEGER DEFAULT 0,
    next_page_token TEXT,
    last_searched_at TIMESTAMP,
    result_count_total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id VARCHAR(20) PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    language VARCHAR(10) DEFAULT 'en',
    vault_uri TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchlist (
    video_id VARCHAR(20) PRIMARY KEY,
    tracking_tier VARCHAR(20) DEFAULT 'HOURLY' CHECK (tracking_tier IN ('HOURLY', 'DAILY', 'WEEKLY')),
    last_tracked_at TIMESTAMP,
    next_track_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE watchlist IS 
'Ghost Tracking: Persistent tracking schedule independent of video retention. 
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
CREATE INDEX IF NOT EXISTS idx_search_queue_fetch ON search_queue(priority DESC, mention_count DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_next_track ON watchlist(next_track_at ASC);
CREATE INDEX IF NOT EXISTS idx_watchlist_tier ON watchlist(tracking_tier, next_track_at ASC);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_entity ON system_events(entity_id, created_at DESC);