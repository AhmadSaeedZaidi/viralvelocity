# API Reference

Complete API documentation for Atlas modules.

## Core Modules

### `atlas.db`

Database connection management.

#### `DatabaseManager`

```python
class DatabaseManager:
    async def initialize() -> None
    async def close() -> None
    async def health_check() -> bool
    async def get_connection() -> AsyncContextManager[AsyncConnection]
```

**Usage:**
```python
from atlas import db

# Health check
is_healthy = await db.health_check()

# Get connection
async with db.get_connection() as conn:
    result = await conn.execute("SELECT * FROM channels")
    
# Cleanup
await db.close()
```

---

### `atlas.vault`

Object storage interface.

#### `VaultStrategy` (Abstract)

```python
class VaultStrategy(ABC):
    def store_json(path: str, data: Any) -> None
    def fetch_json(path: str) -> Optional[dict]
    def list_files(prefix: str) -> List[str]
    def store_visual_evidence(video_id: str, frames: List[Tuple[int, bytes]]) -> None
```

**Implementations:**
- `HuggingFaceVault` - Git LFS + Parquet storage
- `GCSVault` - Google Cloud Storage

**Storage Layout:**
```
vault/
├── metadata/{date}/{video_id}.json     # Raw API responses (date-partitioned)
├── transcripts/{video_id}.json         # Full text content
└── visuals/{video_id}.parquet          # Compressed visual evidence
```

**Usage:**
```python
from atlas import vault

# Store metadata (automatically date-partitioned)
vault.store_metadata("dQw4w9WgXcQ", api_response_data)

# Fetch metadata by date
data = vault.fetch_metadata("dQw4w9WgXcQ", "2026-01-09")

# Store transcript
vault.store_transcript("dQw4w9WgXcQ", {"text": "...", "segments": []})

# Fetch transcript
transcript = vault.fetch_transcript("dQw4w9WgXcQ")

# Store visual evidence (Parquet format)
frames = [(0, b"..."), (1, b"...")]
vault.store_visual_evidence("dQw4w9WgXcQ", frames)

# Store binary data (cold storage)
import io
binary_data = io.BytesIO(b"raw binary content")
uri = vault.store_binary("raw/data.bin", binary_data)
# Returns: "gs://bucket/raw/data.bin" or "hf://datasets/repo/raw/data.bin"

# Fetch binary data (supports URIs)
buffer = vault.fetch_binary("gs://bucket/raw/data.bin")
# or: buffer = vault.fetch_binary("raw/data.bin")
if buffer:
    content = buffer.read()

# Low-level operations
vault.store_json("custom/path.json", {"key": "value"})
data = vault.fetch_json("custom/path.json")
files = vault.list_files("metadata/")
```

---

### `atlas.events`

Event sourcing system.

#### `EventBus`

```python
class EventBus:
    async def emit(event_type: str, entity_id: str, payload: Dict[str, Any]) -> None
```

**Usage:**
```python
from atlas import events

await events.emit(
    event_type="video.discovered",
    entity_id="dQw4w9WgXcQ",
    payload={
        "title": "Example Video",
        "channel_id": "UCxxx",
        "views": 1000
    }
)
```

---

### `atlas.notifications`

Discord webhook notifications.

#### `AlertLevel` (Enum)

```python
class AlertLevel(Enum):
    INFO = 0x3498DB      # Blue
    SUCCESS = 0x2ECC71   # Green
    WARNING = 0xF1C40F   # Yellow
    CRITICAL = 0xE74C3C  # Red
```

#### `AlertChannel` (Enum)

```python
class AlertChannel(Enum):
    ALERTS = "alerts"
    HUNT = "hunt"
    SURVEILLANCE = "watch"
    OPS = "ops"
```

#### `DiscordNotifier`

```python
class DiscordNotifier:
    async def send(
        title: str,
        description: str,
        channel: AlertChannel = AlertChannel.ALERTS,
        level: AlertLevel = AlertLevel.INFO,
        fields: Optional[Dict[str, str]] = None
    ) -> None
```

**Usage:**
```python
from atlas import notifier, AlertLevel, AlertChannel

await notifier.send(
    title="Anomaly Detected",
    description="Unusual velocity on video X",
    channel=AlertChannel.SURVEILLANCE,
    level=AlertLevel.WARNING,
    fields={
        "video_id": "abc123",
        "delta": "+500%",
        "timestamp": "2026-01-07T12:00:00Z"
    }
)
```

---

### `atlas.adapter`

Database adapter base class for clean data access patterns.

#### `DatabaseAdapter`

```python
class DatabaseAdapter:
    async def _execute(query: str, params: Optional[Tuple] = None) -> None
    async def _fetch_one(query: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]
    async def _fetch_all(query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]
    async def _fetch_scalar(query: str, params: Optional[Tuple] = None) -> Any
    async def _execute_many(query: str, params_list: List[Tuple]) -> None
```

**Usage:**
```python
from atlas import DatabaseAdapter

class MyServiceDB(DatabaseAdapter):
    async def get_user(self, user_id: str):
        query = "SELECT * FROM users WHERE id = %s"
        return await self._fetch_one(query, (user_id,))
    
    async def list_users(self):
        query = "SELECT * FROM users ORDER BY created_at DESC"
        return await self._fetch_all(query)
    
    async def count_users(self):
        query = "SELECT COUNT(*) FROM users"
        return await self._fetch_scalar(query)

service_db = MyServiceDB()
```

---

### `atlas.config`

Configuration management.

#### `Settings`

```python
class Settings(BaseSettings):
    # Database
    DATABASE_URL: PostgresDsn
    
    # Storage
    VAULT_PROVIDER: Literal["huggingface", "gcs"]
    HF_DATASET_ID: Optional[str]
    HF_TOKEN: Optional[SecretStr]
    GCS_BUCKET_NAME: Optional[str]
    
    # Governance
    COMPLIANCE_MODE: bool
    ENV: str
    
    # API Keys
    YOUTUBE_API_KEY_POOL_JSON: SecretStr
    KEY_POOL_ARCHEOLOGY_SIZE: int
    KEY_POOL_TRACKING_SIZE: int
    
    # Webhooks
    DISCORD_WEBHOOK_ALERTS: Optional[SecretStr]
    DISCORD_WEBHOOK_HUNT: Optional[SecretStr]
    DISCORD_WEBHOOK_SURVEILLANCE: Optional[SecretStr]
    DISCORD_WEBHOOK_OPS: Optional[SecretStr]
    
    # Orchestration
    PREFECT_API_URL: Optional[str]
    PREFECT_API_KEY: Optional[SecretStr]
    
    @property
    def api_keys(self) -> List[str]
```

**Usage:**
```python
from atlas import settings

# Access configuration
db_url = settings.DATABASE_URL
provider = settings.VAULT_PROVIDER

# API keys (respects compliance mode)
keys = settings.api_keys

# Key rings (functional pools)
rings = settings.key_rings
print(f"Hunting pool: {len(rings['hunting'])} keys")
print(f"Tracking pool: {len(rings['tracking'])} keys")
print(f"Archeology pool: {len(rings['archeology'])} keys")

# Check compliance
if settings.COMPLIANCE_MODE:
    print("Running in compliance mode")
```

---

### `atlas.utils`

Utility functions and classes.

#### Functions

```python
def validate_youtube_id(video_id: str) -> bool
def validate_channel_id(channel_id: str) -> bool
async def health_check_all() -> dict[str, bool]
def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
) -> Callable
```

#### Classes

```python
class KeyRing:
    def __init__(self, pool_name: str)
    def next_key(self) -> str
    @property
    def size(self) -> int
```

**Usage:**
```python
from atlas.utils import (
    validate_youtube_id,
    validate_channel_id,
    retry_async,
    health_check_all,
    KeyRing
)

# Validate IDs
if validate_youtube_id("dQw4w9WgXcQ"):
    print("Valid video ID")

if validate_channel_id("UCuAXFkgsw1L7xaCfnd5JJOw"):
    print("Valid channel ID")

# Health checks
health = await health_check_all()
print(health)  # {"database": True}

# Retry decorator
@retry_async(max_attempts=5, delay=2.0, backoff=2.0)
async def fetch_data():
    return await api_call()

# Key Ring (infinite key rotation)
hunting_keys = KeyRing("hunting")
current_key = hunting_keys.next_key()
print(f"Pool size: {hunting_keys.size}")
```

---

## Database Schema

### Tables

#### `channels`
```sql
CREATE TABLE channels (
    id VARCHAR(50) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    country VARCHAR(10),
    custom_url VARCHAR(100),
    created_at TIMESTAMP,
    is_verified BOOLEAN DEFAULT FALSE,
    last_scraped_at TIMESTAMP DEFAULT NOW()
);
```

#### `videos`
```sql
CREATE TABLE videos (
    id VARCHAR(20) PRIMARY KEY,
    channel_id VARCHAR(50) REFERENCES channels(id),
    title TEXT NOT NULL,
    published_at TIMESTAMP,
    duration INTEGER,
    tags TEXT[],                    -- Indexed via GIN
    category_id VARCHAR(10),        -- e.g., "25" (News)
    default_language VARCHAR(10),   -- e.g., "en"
    wiki_topics TEXT[],
    discovered_at TIMESTAMP DEFAULT NOW(),
    last_updated_at TIMESTAMP       -- Tracker staleness detection
);

-- Indices
CREATE INDEX idx_video_tags ON videos USING GIN(tags);
CREATE INDEX idx_video_category ON videos(category_id);
CREATE INDEX idx_video_tracker_staleness ON videos(last_updated_at ASC NULLS FIRST);
```

#### `video_vectors`
```sql
CREATE TABLE video_vectors (
    video_id VARCHAR(20) REFERENCES videos(id),
    frame_index INTEGER NOT NULL,
    vector vector(512) NOT NULL,
    source_type VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (video_id, frame_index)
);
```

#### `system_events`
```sql
CREATE TABLE system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(50),
    payload JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### `search_queue`
```sql
CREATE TABLE search_queue (
    id SERIAL PRIMARY KEY,
    query_term TEXT UNIQUE NOT NULL,
    priority INTEGER DEFAULT 0,
    mention_count INTEGER DEFAULT 0,
    next_page_token TEXT,
    last_searched_at TIMESTAMP,
    result_count_total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);
```

#### `transcripts`
```sql
CREATE TABLE transcripts (
    video_id VARCHAR(20) PRIMARY KEY REFERENCES videos(id),
    language VARCHAR(10) DEFAULT 'en',
    vault_uri TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Time-Series Tables (TimescaleDB Hypertables)

#### `channel_stats_log`
```sql
CREATE TABLE channel_stats_log (
    channel_id VARCHAR(50) REFERENCES channels(id),
    timestamp TIMESTAMP DEFAULT NOW(),
    view_count BIGINT,
    subscriber_count BIGINT,
    video_count INTEGER,
    PRIMARY KEY (channel_id, timestamp)
);
SELECT create_hypertable('channel_stats_log', 'timestamp');
```

#### `video_stats_log`
```sql
CREATE TABLE video_stats_log (
    video_id VARCHAR(20) REFERENCES videos(id),
    timestamp TIMESTAMP DEFAULT NOW(),
    views BIGINT,
    likes BIGINT,
    comment_count BIGINT,
    PRIMARY KEY (video_id, timestamp)
);
SELECT create_hypertable('video_stats_log', 'timestamp');
```

**TimescaleDB Benefits:**
- Automatic time-based chunking
- Optimized aggregations (avg, sum, count)
- Fast time-range queries
- Compression for historical data

### Audit Tables

#### `channel_history`
```sql
CREATE TABLE channel_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id VARCHAR(50) REFERENCES channels(id),
    changed_at TIMESTAMP DEFAULT NOW(),
    old_title VARCHAR(255),
    new_title VARCHAR(255),
    event_type VARCHAR(50) NOT NULL
);
```

---

## Type Definitions

### Common Types

```python
from typing import Dict, List, Optional, Any, Tuple, AsyncGenerator
from psycopg import AsyncConnection

# Storage
StoragePath = str
VideoID = str
ChannelID = str
Frame = Tuple[int, bytes]  # (frame_index, image_bytes)

# Events
EventType = str
EntityID = str
EventPayload = Dict[str, Any]

# Configuration
VaultProvider = Literal["huggingface", "gcs"]
Environment = str  # "dev" | "prod" | "test"
```

---

## Error Handling

### Database Errors
- Connection failures: Raise with context
- Query errors: Raise with SQL details
- Pool exhaustion: Raise `PoolTimeout`

### Storage Errors
- Upload failures: Raise with path
- Download failures: Return `None`
- List failures: Return empty list `[]`

### Event Errors
- Swallowed (non-blocking)
- Logged at ERROR level

### Notification Errors
- Swallowed (non-blocking)
- Logged at ERROR level

---

## Configuration Options

See [ENV.example](../ENV.example) for complete configuration template.

### Required Variables
- `DATABASE_URL`
- `YOUTUBE_API_KEY_POOL_JSON`
- `VAULT_PROVIDER`

### Provider-Specific
**HuggingFace:**
- `HF_DATASET_ID`
- `HF_TOKEN`

**Google Cloud Storage:**
- `GCS_BUCKET_NAME`
- `GOOGLE_APPLICATION_CREDENTIALS` (environment)

### Optional
- `COMPLIANCE_MODE` (default: `true`)
- `ENV` (default: `dev`)
- `DISCORD_WEBHOOK_*`
- `PREFECT_API_URL`
- `PREFECT_API_KEY`

