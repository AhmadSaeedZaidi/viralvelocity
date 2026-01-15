# Hydra Protocol

**Intelligent API key management and graceful termination**

The Hydra Protocol is Pleiades' intelligent API key management system that handles quota exhaustion, automatic key rotation, and clean service termination.

---

## Overview

### The Problem

YouTube Data API v3 has strict quota limits (10,000 units/day per key). Traditional approaches:
- ❌ Hard-code single API key
- ❌ Crash on quota exhaustion
- ❌ No automatic recovery
- ❌ Manual intervention required

### The Solution

**Hydra Protocol** provides:
- ✅ Multi-key pool management (KeyRing)
- ✅ Automatic key rotation on failure
- ✅ Clean termination when all keys exhausted (SystemExit)
- ✅ Retry logic with exponential backoff
- ✅ Container orchestration integration

Named after the mythological Hydra: "Cut off one head, two more grow back" - When one key fails, rotate to the next.

---

## Architecture

### Components

#### 1. KeyRing

Manages the API key pool:

```python
from atlas.utils import KeyRing

# Initialize with key pool name
hunter_keys = KeyRing("hunting")  # Uses YOUTUBE_API_KEY_POOL_JSON

# Get current key
current_key = hunter_keys.current()

# Rotate to next key
hunter_keys.rotate()

# Check if keys remain
if hunter_keys.exhausted:
    # All keys exhausted!
    pass
```

#### 2. HydraExecutor

Executes requests with automatic retry and rotation:

```python
from atlas.utils import HydraExecutor, KeyRing

keys = KeyRing("hunting")
executor = HydraExecutor(keys, agent_name="hunter")

# Define request function
async def make_request(api_key: str):
    params = {"key": api_key, "q": "viral videos"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status in (403, 429):
                # Quota exhausted or rate limited
                raise Exception(f"HTTP {resp.status}")
            else:
                raise Exception(f"HTTP {resp.status}")

# Execute with automatic retry/rotation
try:
    result = await executor.execute_async(make_request)
except SystemExit:
    # All keys exhausted - clean termination
    logger.critical("Hydra Protocol: All API keys exhausted")
    raise
```

---

## Behavior

### Normal Operation

```
Request 1: Key A → Success ✓
Request 2: Key A → Success ✓
Request 3: Key A → Success ✓
```

### Single Key Failure

```
Request 4: Key A → 403 Quota Exceeded ✗
  ↓ Rotate
Request 4: Key B → Success ✓
Request 5: Key B → Success ✓
```

### All Keys Exhausted

```
Request N: Key Z → 403 Quota Exceeded ✗
  ↓ No more keys
  ↓ Raise SystemExit(code=42)
  ↓ Container terminates cleanly
  ↓ Orchestrator detects exit code 42
  ↓ Does NOT restart (intentional stop)
```

---

## Integration

### Hunter Agent

```python
from atlas.utils import KeyRing, HydraExecutor

# Initialize
hunter_keys = KeyRing("hunting")
hunter_executor = HydraExecutor(hunter_keys, agent_name="hunter")

@flow(name="run_hunter_cycle")
async def run_hunter_cycle():
    try:
        async def make_request(api_key: str):
            # Make API call with key
            ...
        
        result = await hunter_executor.execute_async(make_request)
        
    except SystemExit:
        # Hydra Protocol termination - propagate immediately
        logger.critical("Hunter terminated: All API keys exhausted")
        raise  # Clean exit
```

### Tracker Agent

```python
from atlas.utils import KeyRing, HydraExecutor

# Separate key pool for Tracker
tracker_keys = KeyRing("tracking")
tracker_executor = HydraExecutor(tracker_keys, agent_name="tracker")

@flow(name="run_tracker_cycle")
async def run_tracker_cycle():
    try:
        result = await tracker_executor.execute_async(make_request)
    except SystemExit:
        logger.critical("Tracker terminated: All API keys exhausted")
        raise
```

---

## Configuration

### Environment Variables

```bash
# API Key Pool (JSON array)
YOUTUBE_API_KEY_POOL_JSON='[
  "AIzaSyABC123...",
  "AIzaSyDEF456...",
  "AIzaSyGHI789..."
]'

# Hydra Settings
HYDRA_ENABLED=true
HYDRA_RETRY_ATTEMPTS=3
HYDRA_BACKOFF_FACTOR=2
```

### Validation

```python
from atlas.config import settings

# Check keys loaded
assert len(settings.api_keys) > 0
assert all(len(key) > 10 for key in settings.api_keys)
```

---

## Exit Codes

The Hydra Protocol uses specific exit codes:

| Exit Code | Meaning | Action |
|-----------|---------|--------|
| 0 | Normal exit | - |
| 1 | Unexpected error | Restart container |
| 42 | Hydra Protocol: All keys exhausted | Do NOT restart |
| 130 | SIGINT (Ctrl+C) | User-initiated stop |

### Docker Compose Integration

```yaml
services:
  maia-hunter:
    image: pleiades/maia:latest
    restart: on-failure:5  # Restart on error, but NOT on exit 42
    deploy:
      restart_policy:
        condition: on-failure
        max_attempts: 5
```

### Kubernetes Integration

```yaml
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: maia-hunter
    image: pleiades/maia:latest
  restartPolicy: OnFailure  # Restart only on non-zero exit (except our protocol codes)
```

---

## Key Management

### Best Practices

#### 1. Separate Pools per Agent

```bash
# Hunter keys (high volume)
HUNTER_KEY_POOL='["key1", "key2", "key3"]'

# Tracker keys (lower volume)
TRACKER_KEY_POOL='["key4", "key5"]'
```

#### 2. Monitor Quota Usage

```python
# Log key rotations
logger.warning(f"Rotated to next API key (exhausted: {keys.exhausted_count})")
```

#### 3. Set Quotas in GCP Console

- Enable YouTube Data API v3
- Set quota to 10,000 units/day per key
- Monitor usage in GCP Console

#### 4. Handle Exhaustion Gracefully

```python
except SystemExit as e:
    # Log for monitoring
    logger.critical(f"Hydra Protocol triggered: {e}")
    
    # Alert via notifier
    await notifier.send(
        level="critical",
        message="All API keys exhausted",
        metadata={"service": "hunter"}
    )
    
    # Clean exit
    raise
```

---

## Monitoring

### Key Pool Health

```python
# Check remaining keys
healthy_keys = len(keys.pool) - keys.exhausted_count
logger.info(f"Healthy keys: {healthy_keys}/{len(keys.pool)}")

# Alert if low
if healthy_keys < 2:
    await notifier.send(
        level="warning",
        message=f"Low key availability: {healthy_keys} remaining"
    )
```

### Hydra Events

```python
# Subscribe to key rotation events
@events.on("hydra.key_rotated")
async def on_key_rotated(data: dict):
    logger.warning(f"Key rotated: {data}")
    # Send to monitoring system

@events.on("hydra.pool_exhausted")
async def on_pool_exhausted(data: dict):
    logger.critical(f"Pool exhausted: {data}")
    # Alert operations team
```

---

## Testing

### Unit Tests

```python
@pytest.mark.asyncio
async def test_keyring_rotation():
    """Test KeyRing rotates through keys."""
    keys = KeyRing("test", pool=["key1", "key2", "key3"])
    
    assert keys.current() == "key1"
    keys.rotate()
    assert keys.current() == "key2"
    keys.rotate()
    assert keys.current() == "key3"
    
    # Should wrap around
    keys.rotate()
    assert keys.current() == "key1"

@pytest.mark.asyncio
async def test_hydra_executor_retries():
    """Test HydraExecutor retries on failure."""
    keys = KeyRing("test", pool=["key1", "key2"])
    executor = HydraExecutor(keys)
    
    call_count = 0
    
    async def failing_request(api_key: str):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("HTTP 403")
        return {"success": True}
    
    result = await executor.execute_async(failing_request)
    assert result["success"]
    assert call_count == 3  # Failed twice, succeeded on third
```

### Integration Tests

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hunter_hydra_protocol():
    """Test Hunter raises SystemExit on key exhaustion."""
    with patch("maia.hunter.hunter_keys") as mock_keys:
        mock_keys.exhausted = True
        
        with pytest.raises(SystemExit, match="42"):
            await run_hunter_cycle()
```

---

## Troubleshooting

### All Keys Exhausted Immediately

**Symptom**: SystemExit on first request  
**Cause**: Invalid API keys or project not configured  
**Fix**: 
```bash
# Verify keys
python -c "from atlas.config import settings; print(settings.api_keys)"

# Test key manually
curl "https://www.googleapis.com/youtube/v3/search?part=snippet&q=test&key=YOUR_KEY"
```

### Keys Not Rotating

**Symptom**: Same key used repeatedly  
**Cause**: Rotation not triggered on failure  
**Fix**: Ensure errors raise exceptions that HydraExecutor can catch

### Exit Code Not Respected

**Symptom**: Container restarts despite exit 42  
**Cause**: Orchestrator not configured correctly  
**Fix**: Set `restart_policy: on-failure` in Docker Compose

---

## Summary

**Hydra Protocol** enables resilient API usage:

- ✅ Multi-key pool management
- ✅ Automatic rotation on quota exhaustion  
- ✅ Clean termination (exit 42)
- ✅ Retry logic with backoff
- ✅ Container orchestration integration

**Result**: Maximize API throughput while respecting quotas and enabling clean shutdowns.
