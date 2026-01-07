CREATE EXTENSION IF NOT EXISTS vector;

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
    wiki_topics TEXT[],
    discovered_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_stats_log (
    video_id VARCHAR(20) REFERENCES videos(id) ON DELETE CASCADE,
    timestamp TIMESTAMP DEFAULT NOW(),
    views BIGINT,
    likes BIGINT,
    comment_count BIGINT,
    PRIMARY KEY (video_id, timestamp)
);

CREATE TABLE IF NOT EXISTS video_vectors (
    video_id VARCHAR(20) REFERENCES videos(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    vector vector(512) NOT NULL,
    source_type VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (video_id, frame_index)
);

CREATE TABLE IF NOT EXISTS system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(50),
    payload JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_publish ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_scrape ON channels(last_scraped_at ASC);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_entity ON system_events(entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_history_channel ON channel_history(channel_id, changed_at DESC);