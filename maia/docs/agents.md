# Maia Agents - Complete Reference

This document provides a comprehensive overview of all Maia agents, their roles, methods, and implementation details.

## Overview

Maia consists of **5 specialized agents**, each with a specific role in the video intelligence pipeline:

| Agent | Role | Method | Key Feature |
|-------|------|--------|-------------|
| **Hunter** | Discovery & Ingestion | Snowball Sampling | Recursive tag extraction |
| **Tracker** | Velocity Monitoring | 3-Zone Defense | Freshness-based prioritization |
| **Archeologist** | Historical Curation | Grave Robbery | High-priority historical discovery |
| **Scribe** | Transcription | Priority-based fetching | Manual > Generated transcripts |
| **Painter** | Visual Archival | Intelligent Keyframe Extraction | Chapter + Heatmap + Fallback |

---

## 1. The Hunter

**File**: `maia/src/maia/hunter.py`

### Purpose
Discover new video content through YouTube search queries and implement recursive discovery via the "Snowball Effect".

### Method: Snowball Sampling
1. Fetch queries from `search_queue` (using `FOR UPDATE SKIP LOCKED`)
2. Search YouTube API with automatic key rotation
3. Ingest video metadata to Atlas (hot index + cold archive)
4. Extract tags from discovered videos
5. Feed unique tags back into `search_queue` (recursive discovery)

### Key Features
- **Race-free concurrency**: `FOR UPDATE SKIP LOCKED` prevents duplicate processing
- **Automatic key rotation**: Cycles through hunting key ring on 403
- **Stale token reset**: Discards pagination tokens >12h old
- **Cold archive**: Stores raw API responses to vault for reproducibility
- **Hot index**: Structured metadata in database for fast queries

### Hydra Protocol
```python
if resp.status == 429:
    logger.critical("HIT 429 RATE LIMIT. INITIATING CHURN & BURN.")
    raise SystemExit("429 Rate Limit - Container Suicide")
```

### Configuration
- **Key Ring**: `hunting` (from `atlas.utils.KeyRing`)
- **Batch Size**: 10 queries (configurable)
- **Results per query**: 50 videos (YouTube API limit)

### Entry Points
```bash
# CLI
maia-hunter

# Python
python -m maia hunter

# Programmatic
from maia import run_hunter_cycle
await run_hunter_cycle(batch_size=10)
```

---

## 2. The Tracker

**File**: `maia/src/maia/tracker.py`

### Purpose
Monitor viral velocity by updating view counts, likes, and engagement metrics based on video age.

### Method: 3-Zone Defense
Videos are prioritized by freshness:

| Zone | Age | Update Frequency | Priority |
|------|-----|------------------|----------|
| **Zone 1 (Hot)** | <24h | Hourly | 1 (Highest) |
| **Zone 2 (Warm)** | 1-7 days | Every 6 hours | 2 |
| **Zone 3 (Cold)** | >7 days | Daily | 3 |

### Workflow
1. `fetch_targets()` - Gets stale videos based on 3-Zone Defense
2. `update_stats()` - Batches up to 50 video IDs per API call
3. Updates both:
   - `videos.last_updated_at` (timestamp)
   - `video_stats_log` (TimescaleDB hypertable - time-series data)

### Key Features
- **Batch size enforcement**: Max 50 for YouTube API
- **Priority-based fetching**: Zone 1 > Zone 2 > Zone 3
- **Graceful degradation**: Handles deleted/private videos
- **Time-series storage**: Hypertable optimized for analytics

### Configuration
- **Key Ring**: `tracking` (separate from hunting)
- **Batch Size**: 50 videos (YouTube API limit)

### Entry Points
```bash
# CLI
maia-tracker

# Python
python -m maia tracker

# Programmatic
from maia import run_tracker_cycle
await run_tracker_cycle(batch_size=50)
```

---

## 3. The Archeologist

**File**: `maia/src/maia/archeologist.py`

### Purpose
Curate historically significant content by scanning YouTube's archive for top videos in specific categories.

### Method: Grave Robbery
Systematically searches history month-by-month for the last 20 years:
1. Target specific categories (Gaming, Entertainment, Music, Tech, Education)
2. Search by `viewCount` order (find the "gold")
3. Ingest with **priority=100** (highest priority for other agents)

### Target Categories
```python
TARGET_CATEGORIES = [
    "10",  # Music
    "20",  # Gaming
    "24",  # Entertainment
    "28",  # Science & Technology
    "27",  # Education
]
```

### Key Features
- **High-priority ingestion**: Sets `priority=100` on discovered videos
- **Historical range**: Configurable start/end years (default: 2005-2024)
- **Category-based**: Focuses on culturally significant categories
- **Quota-intensive**: WARNING - consumes massive quota, run sparingly

### Configuration
- **Key Ring**: `archeology` (dedicated pool)
- **Results per search**: 50 videos
- **Default range**: 2005-2024 (20 years)

### Entry Points
```bash
# Python (no CLI entry point - run carefully)
from maia import run_archeology_campaign
await run_archeology_campaign(start_year=2010, end_year=2010)
```

### Usage Warning
⚠️ **CAUTION**: The Archeologist consumes massive API quota. One full campaign (20 years × 12 months × 5 categories = 1,200 searches) will exhaust multiple API keys. Run sparingly and strategically.

---

## 4. The Scribe

**Files**: 
- `maia/src/maia/scribe/loader.py` (Transcript fetching)
- `maia/src/maia/scribe/flow.py` (Orchestration)

### Purpose
Fetch and archive video transcripts/captions for text analysis.

### Method: Priority-based Transcript Fetching
Transcript priority order:
1. **Manual English** (highest quality)
2. **Generated English** (better than nothing)
3. **Manual Foreign** (es, fr, de, pt, ru, ja, ko)

### Workflow
1. `fetch_scribe_targets()` - Gets videos needing transcripts
2. `process_transcript()` - Fetches transcript via `youtube-transcript-api`
3. Stores full JSON structure in vault
4. Marks status in database (`available` or `unavailable`)

### Key Features
- **Hydra Protocol**: Raises `SystemExit` on `TooManyRequests`
- **Quality prioritization**: Prefers manual over generated
- **Graceful handling**: Marks videos without captions as `unavailable`
- **Async execution**: Runs blocking API calls in executor

### Hydra Protocol
```python
except TooManyRequests:
    logger.critical("IP BLOCKED by YouTube. Initiating Hydra Protocol.")
    raise SystemExit("429 Rate Limit (Scribe) - Container Suicide")
```

### Configuration
- **Batch Size**: 10 videos (small due to rate limits)
- **Processing**: Sequential (to manage rate limits gently)

### Entry Points
```bash
# Python
from maia import run_scribe_cycle
await run_scribe_cycle()
```

---

## 5. The Painter

**File**: `maia/src/maia/painter.py`

### Purpose
Extract and archive intelligent keyframes from videos for visual analysis.

### Method: Intelligent Keyframe Extraction
Multi-strategy approach:

#### Strategy A: Structure (Chapters)
- Extracts frames at chapter start times
- Captures natural content divisions

#### Strategy B: Viral Peaks (Heatmap)
- Parses YouTube's heatmap data
- Extracts frames at top 5 viewership intensity points
- Captures "viral moments"

#### Strategy C: Fallback (Duration-based)
If no chapters/heatmap available:
- Short videos (<10 min): 5 frames
- Medium videos (10-30 min): 10 frames
- Long videos (>30 min): 20 frames
- Evenly distributed across duration

### Workflow
1. **Frame -1**: Fetch official thumbnail (`maxresdefault.jpg`)
2. **Get video info**: Stream URL, chapters, heatmap via `yt-dlp`
3. **Determine timestamps**: Apply strategies A, B, C
4. **Extract frames**: Use OpenCV to seek and capture
5. **Store**: Encode as JPG, store in vault, log metadata in DB

### Key Features
- **Intelligent extraction**: Context-aware keyframe selection
- **Efficient seeking**: Sorted timestamps for sequential processing
- **Metadata logging**: Stores frame dimensions and vault URI
- **Scalable**: Frame count scales with video length

### Configuration
- **Batch Size**: 5 videos (small due to heavy processing)
- **Encoding**: JPG format
- **Top viral peaks**: 5 (configurable)

### Entry Points
```bash
# Python
from maia import run_painter_cycle
await run_painter_cycle()
```

---

## Common Patterns

### 1. Hydra Protocol Implementation
All agents that interact with external APIs implement the Hydra Protocol:

```python
try:
    # API call
    async with session.get(url, params=params) as resp:
        if resp.status == 429:
            logger.critical("HIT 429. INITIATING HYDRA PROTOCOL.")
            raise SystemExit("429 Rate Limit - Agent Name")
except SystemExit:
    # DON'T CATCH - must propagate
    raise
except Exception as e:
    # Other errors can be handled
    logger.error(f"Error: {e}")
```

### 2. Key Rotation
All agents use dedicated key rings:

```python
from atlas.utils import KeyRing

agent_keys = KeyRing("agent_name")  # hunting, tracking, archeology

max_retries = agent_keys.size
for _ in range(max_retries):
    key = agent_keys.next_key()
    # Use key...
```

### 3. DAO Pattern
All database access through MaiaDAO:

```python
from atlas.adapters.maia import MaiaDAO

dao = MaiaDAO()
batch = await dao.fetch_agent_batch(batch_size)
await dao.store_agent_data(data)
```

### 4. Vault Storage
All cold storage through Atlas vault:

```python
from atlas import vault

# Store metadata
vault.store_metadata(video_id, api_response)

# Store text
vault_uri = vault.store_text(video_id, text_data, extension="json")

# Store binary
vault_uri = vault.store_blob(video_id, binary_data, extension="jpg")
```

---

## Agent Coordination

### Priority System
Videos have a `priority` field that determines processing order:

| Priority | Source | Processing |
|----------|--------|------------|
| **100** | Archeologist | Immediate (highest) |
| **5-10** | Hunter (Snowball) | Normal |
| **0** | Default | Standard queue |

### Processing Flow
```
1. Hunter discovers videos → priority=0-10
2. Archeologist finds historical gems → priority=100
3. Tracker monitors all videos (3-Zone Defense)
4. Scribe fetches transcripts (as needed)
5. Painter extracts keyframes (as needed)
```

### Key Ring Isolation
Separate key pools prevent quota contamination:

```
Total Keys: 10
├── Hunting: 7 keys (high volume)
├── Tracking: 2 keys (moderate volume)
└── Archeology: 1 key (low volume, high value)
```

---

## Development Guidelines

### Adding a New Agent

1. **Create agent file**: `maia/src/maia/new_agent.py`
2. **Implement Prefect flow**:
   ```python
   from prefect import flow, task, get_run_logger
   from atlas.adapters.maia import MaiaDAO
   
   @task(name="agent_task")
   async def agent_task():
       dao = MaiaDAO()
       # Implementation
   
   @flow(name="run_agent_cycle")
   async def run_agent_cycle():
       logger = get_run_logger()
       # Orchestration
   ```

3. **Add to `__init__.py`**:
   ```python
   from maia.new_agent import run_agent_cycle
   __all__.append("run_agent_cycle")
   ```

4. **Add DAO methods**: In `atlas/src/atlas/adapters/maia.py`
5. **Add tests**: Unit tests in `maia/tests/`, integration in `alkyone/`
6. **Update documentation**: This file and README

### Best Practices

1. **Always use MaiaDAO** - Never write raw SQL
2. **Implement Hydra Protocol** - Raise SystemExit on 429
3. **Use dedicated KeyRing** - Prevent quota contamination
4. **Store to vault** - Keep raw data for reproducibility
5. **Log comprehensively** - Use `get_run_logger()`
6. **Handle errors gracefully** - Non-critical failures shouldn't crash
7. **Type everything** - Complete type hints for all functions

---

## Troubleshooting

### Agent Won't Start
- Check Atlas is installed: `pip install -e ../atlas`
- Verify environment variables in `.env`
- Check key ring configuration

### Rate Limits
- **Expected behavior**: Hydra Protocol triggers container restart
- **Prevention**: Use separate key rings per agent
- **Monitoring**: Check logs for 403/429 responses

### Missing Data
- **Hunter**: Check `search_queue` has queries
- **Tracker**: Verify videos exist in database
- **Scribe**: Some videos legitimately have no captions
- **Painter**: Requires video stream access (may fail for private videos)

### Performance Issues
- **Reduce batch sizes**: Especially for Painter (heavy CV2 operations)
- **Check network**: Vault operations can be slow
- **Monitor quotas**: Track API usage across all agents

---

## Future Enhancements

### Planned Agents
- **Analyst**: ML-based content analysis
- **Curator**: Playlist and collection management
- **Sentinel**: Anomaly detection and alerting

### Planned Features
- **Adaptive batching**: Dynamic batch sizes based on quota
- **Circuit breakers**: Temporary key suspension
- **Metrics dashboard**: Real-time agent monitoring
- **Smart scheduling**: Time-of-day optimization

---

**Version**: 0.1.0  
**Last Updated**: 2026-01-10  
**Maintainer**: Ahmad Saeed Zaidi



