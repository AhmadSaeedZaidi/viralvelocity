-- Enable pgvector for visual embeddings (The "Vectorize & Vanish" requirement)
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. CHANNELS (The Entities)
CREATE TABLE IF NOT EXISTS channels (
    id VARCHAR(50) PRIMARY KEY, -- YouTube Channel ID
    title VARCHAR(255) NOT NULL,
    country VARCHAR(10),
    custom_url VARCHAR(100),
    created_at TIMESTAMP,
    is_verified BOOLEAN DEFAULT FALSE,
    last_scraped_at TIMESTAMP DEFAULT NOW()
);

-- 2. CHANNEL HISTORY (The Audit Trail)
CREATE TABLE IF NOT EXISTS channel_history (
    id UUID PRIMARY KEY,
    channel_id VARCHAR(50) REFERENCES channels(id),
    changed_at TIMESTAMP DEFAULT NOW(),
    old_title VARCHAR(255),
    new_title VARCHAR(255),
    event_type VARCHAR(50) -- 'rebrand', 'handle_change'
);

-- 3. CHANNEL STATS LOG (Time-Series)
CREATE TABLE IF NOT EXISTS channel_stats_log (
    channel_id VARCHAR(50) REFERENCES channels(id),
    timestamp TIMESTAMP DEFAULT NOW(),
    view_count BIGINT,
    subscriber_count BIGINT,
    video_count INTEGER,
    PRIMARY KEY (channel_id, timestamp)
);

-- 4. VIDEOS (The Content)
CREATE TABLE IF NOT EXISTS videos (
    id VARCHAR(20) PRIMARY KEY, -- YouTube Video ID
    channel_id VARCHAR(50) REFERENCES channels(id),
    title TEXT NOT NULL,
    published_at TIMESTAMP,
    duration INTEGER,
    wiki_topics TEXT[], -- Knowledge Graph Anchors
    discovered_at TIMESTAMP DEFAULT NOW()
);

-- 5. VIDEO STATS LOG (Time-Series for Velocity Mismatch)
CREATE TABLE IF NOT EXISTS video_stats_log (
    video_id VARCHAR(20) REFERENCES videos(id),
    timestamp TIMESTAMP DEFAULT NOW(),
    views BIGINT,
    likes BIGINT,
    comment_count BIGINT,
    PRIMARY KEY (video_id, timestamp)
);

-- 6. VIDEO VECTORS (The Visuals)
CREATE TABLE IF NOT EXISTS video_vectors (
    video_id VARCHAR(20) REFERENCES videos(id),
    frame_index INTEGER,
    vector vector(512), -- CLIP Embedding
    source_type VARCHAR(50), -- 'heatmap', 'chapter'
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (video_id, frame_index)
);

-- 7. SYSTEM EVENTS (Event Sourcing Log)
CREATE TABLE IF NOT EXISTS system_events (
    id UUID PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(50),
    payload JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Performance Indices
CREATE INDEX IF NOT EXISTS idx_video_publish ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_scrape ON channels(last_scraped_at ASC);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events(event_type, created_at DESC);