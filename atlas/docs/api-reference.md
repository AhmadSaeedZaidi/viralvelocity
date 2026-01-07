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

**Usage:**
```python
from atlas import vault

# Store JSON
vault.store_json("metadata/video.json", {"id": "abc", "title": "Example"})

# Fetch JSON
data = vault.fetch_json("metadata/video.json")

# List files
files = vault.list_files("metadata/")

# Store visual evidence
frames = [(0, b"..."), (1, b"...")]
vault.store_visual_evidence("video123", frames)
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

# Check compliance
if settings.COMPLIANCE_MODE:
    print("Running in compliance mode")
```

---

### `atlas.utils`

Utility functions.

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

**Usage:**
```python
from atlas.utils import (
    validate_youtube_id,
    validate_channel_id,
    retry_async,
    health_check_all
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
    # Will retry up to 5 times with exponential backoff
    return await api_call()
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
    wiki_topics TEXT[],
    discovered_at TIMESTAMP DEFAULT NOW()
);
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

### Time-Series Tables

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
```

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

