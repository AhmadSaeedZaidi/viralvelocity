# Atlas Architecture

## Overview

Atlas is the infrastructure kernel for the Pleiades platform, providing stateless, reusable components for database access, object storage, event sourcing, and system notifications.

## Design Principles

### 1. Stateless Library Pattern
Atlas maintains no internal state beyond configuration. All state is externalized to:
- PostgreSQL (structured data + events)
- Object storage (unstructured data)
- Environment variables (configuration)

### 2. Singleton Services
Core services use the singleton pattern for resource pooling:
- `db`: Single connection pool instance
- `vault`: Single storage provider instance
- `events`: Single event bus instance
- `notifier`: Single notification dispatcher

### 3. Strategy Pattern for Storage
The vault system uses the Strategy pattern to enable swappable storage backends:

```
VaultStrategy (ABC)
    ├── HuggingFaceVault (Research/Archival)
    └── GCSVault (Enterprise/Production)
```

Selection via `VAULT_PROVIDER` environment variable.

## Component Architecture

### Database Layer (`atlas.db`)

**Pattern**: Singleton + Connection Pool  
**Technology**: psycopg3 with AsyncConnectionPool  
**Configuration**:
- `min_size=0` for serverless autoscaling
- `max_size=20` for connection limiting
- Async-only interface

**Lifecycle**:
```python
await db.initialize()  # Explicit init (or lazy via get_connection)
async with db.get_connection() as conn:
    await conn.execute(...)
await db.close()  # Cleanup
```

**Thread Safety**: Single instance across entire application lifecycle.

### Storage Layer (`atlas.vault`)

**Pattern**: Strategy + Factory  
**Interface**: `VaultStrategy` ABC

**Operations**:
- `store_json(path, data)`: Store JSON metadata
- `fetch_json(path)`: Retrieve JSON metadata
- `list_files(prefix)`: List stored files
- `store_visual_evidence(video_id, frames)`: Archive visual data

**Providers**:

#### HuggingFace Vault
- **Use Case**: Research, unlimited history, public datasets
- **Storage**: Git LFS + Parquet
- **Dependencies**: `huggingface-hub`, `pandas`, `pyarrow`
- **Advantages**: Version control, infinite retention, free for public datasets

#### GCS Vault
- **Use Case**: Enterprise, production workloads
- **Storage**: Google Cloud Storage buckets
- **Dependencies**: `google-cloud-storage`
- **Advantages**: High availability, integrated with GCP ecosystem

**Provider Selection**:
```python
# Factory function
def get_vault() -> VaultStrategy:
    if settings.VAULT_PROVIDER == "gcs":
        return GCSVault()
    return HuggingFaceVault()
```

### Event Bus (`atlas.events`)

**Pattern**: Fire-and-forget async logging  
**Purpose**: Immutable audit trail for system events

**Schema**:
```sql
CREATE TABLE system_events (
    id UUID PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(50),
    payload JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Usage**:
```python
await events.emit(
    event_type="video.discovered",
    entity_id="dQw4w9WgXcQ",
    payload={"title": "...", "channel_id": "..."}
)
```

**Error Handling**: Swallows exceptions to prevent pipeline disruption.

### Notification System (`atlas.notifications`)

**Pattern**: Channel-based routing  
**Technology**: Discord webhooks via aiohttp

**Alert Levels**:
- `INFO` (blue)
- `SUCCESS` (green)
- `WARNING` (yellow)
- `CRITICAL` (red)

**Channels**:
- `ALERTS`: General system alerts
- `HUNT`: Content discovery notifications
- `SURVEILLANCE`: Monitoring/tracking updates
- `OPS`: Operational/infrastructure alerts

**Fallback Logic**: If channel-specific webhook is unavailable, routes to ALERTS.

**Usage**:
```python
await notifier.send(
    title="Anomaly Detected",
    description="Unusual velocity on video X",
    channel=AlertChannel.SURVEILLANCE,
    level=AlertLevel.WARNING,
    fields={"video_id": "...", "delta": "+500%"}
)
```

### Configuration (`atlas.config`)

**Pattern**: Pydantic Settings with validation  
**Source**: Environment variables + `.env` file

**Validation**:
- Provider-specific requirements enforced via `@model_validator`
- SecretStr for sensitive values
- Type checking for all fields

**Compliance Mode**:
When `COMPLIANCE_MODE=true`:
- API key pool limited to first key
- Signals 30-day retention enforcement to consumers
- Demonstrates standard quota adherence

## Data Flow

### Write Path (Ingestion)
```
Service → vault.store_json() → [HF/GCS]
       ↓
       → events.emit() → PostgreSQL.system_events
       ↓
       → notifier.send() → Discord
```

### Read Path (Query)
```
Service → db.get_connection() → PostgreSQL
       ↓
       → vault.fetch_json() → [HF/GCS]
```

## Schema Architecture

### Core Tables

**Entities**:
- `channels`: YouTube channel metadata
- `videos`: Video metadata with discovery timestamps

**Time-Series**:
- `channel_stats_log`: Channel metrics over time
- `video_stats_log`: Video metrics over time

**Visual Embeddings**:
- `video_vectors`: 512-dim CLIP embeddings (pgvector)

**Audit Trail**:
- `channel_history`: Channel rebrand/handle changes
- `system_events`: Immutable event log (JSONB payloads)

**Indices**:
- `idx_video_publish`: Temporal queries on videos
- `idx_channel_scrape`: Staleness detection
- `idx_events_type`: Event type filtering
- `idx_events_entity`: Entity-specific event lookup

## Error Handling Strategy

### Database
- Connection failures: Log and raise (critical path)
- Query errors: Raise with context

### Storage
- Upload failures: Log and raise
- Download failures: Return None (non-critical)
- List failures: Return empty list

### Events
- Swallow all exceptions (observability is non-blocking)

### Notifications
- Swallow all exceptions (alerts should never block pipeline)

## Extension Points

### Adding a New Vault Provider

1. Implement `VaultStrategy` interface
2. Add to factory function
3. Add configuration validation
4. Update `VAULT_PROVIDER` Literal type

Example:
```python
class S3Vault(VaultStrategy):
    def __init__(self):
        # Initialize boto3 client
        pass
    
    def store_json(self, path: str, data: Any) -> None:
        # Implementation
        pass
    # ... implement other methods

def get_vault() -> VaultStrategy:
    if settings.VAULT_PROVIDER == "s3":
        return S3Vault()
    elif settings.VAULT_PROVIDER == "gcs":
        return GCSVault()
    return HuggingFaceVault()
```

### Adding a New Notification Channel

1. Add enum value to `AlertChannel`
2. Add webhook URL to Settings
3. Update `DiscordNotifier.hooks` mapping

## Performance Characteristics

### Database
- Connection pool: O(1) acquisition from pool
- Serverless scaling: 0-20 connections
- Typical latency: 10-50ms (Neon serverless)

### Storage
- HF: 100-500ms (Git operations + LFS)
- GCS: 20-100ms (direct object storage)

### Events
- Async insert: 5-15ms (non-blocking)

### Notifications
- HTTP webhook: 50-200ms (fire-and-forget)

## Security Considerations

1. **Secrets Management**: All secrets in `SecretStr` (prevents logging)
2. **SQL Injection**: Using parameterized queries exclusively
3. **Compliance**: Provider validation ensures proper credentials
4. **Least Privilege**: Connection pool limits resource consumption

## Dependencies

### Core (Always Required)
- `pydantic` + `pydantic-settings`: Configuration
- `psycopg[binary,pool]`: Database
- `aiohttp`: HTTP client
- `orjson`: Fast JSON serialization

### Optional (Provider-Specific)
- `huggingface-hub`, `pandas`, `pyarrow`: HF vault
- `google-cloud-storage`: GCS vault
- `prefect`: Orchestration (future)

## Testing Strategy

- **Unit Tests**: Mock external dependencies
- **Integration Tests**: Require live database + storage
- **Fixtures**: `conftest.py` provides test environment

## Future Enhancements

1. **Caching Layer**: Redis for frequently accessed vault data
2. **Metrics**: Prometheus instrumentation
3. **Circuit Breakers**: Fault tolerance for storage providers
4. **Read Replicas**: Scale read queries independently
5. **Event Replay**: Reconstruct database from event log


