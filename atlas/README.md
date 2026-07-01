# Atlas

Infrastructure library for the Pleiades platform. Provides PostgreSQL connection management, object storage (HF/GCS), event sourcing, Discord notifications, and YouTube API helpers.

## Architecture

```
atlas/
├── models/        # Pydantic domain models (Channel, Video, VideoStats, etc.)
├── repositories/  # Repository pattern — domain-focused data access
│   ├── VideoRepository
│   ├── ChannelRepository
│   ├── SearchQueueRepository
│   ├── WatchlistRepository
│   └── EventRepository
├── adapters/      # DatabaseAdapter base class (low-level SQL primitives)
├── db.py          # DatabaseManager — async PostgreSQL connection pool
├── vault.py       # VaultStrategy — HF/GCS object storage (Strategy pattern)
├── events.py      # EventBus — immutable event sourcing
├── notifications.py  # DiscordNotifier — channel-based webhook routing
├── utils.py       # KeyRing, ResiliencyExecutor, retry helpers
├── youtube.py     # YouTube Data API v3 helpers
├── config.py      # Pydantic Settings
└── schema.sql     # PostgreSQL schema (TimescaleDB hypertables)
```

**Design patterns:** Repository, DAO, Singleton, Strategy, Protocol.

## Quick Start

```bash
cd atlas
poetry install --extras all --with dev
cp ENV.example .env   # edit with your credentials
make setup            # provision database schema
make test             # run unit tests
```

## Usage

```python
from atlas.repositories import VideoRepository
from atlas.models import Video

repo = VideoRepository()

# Fetch videos needing transcripts
videos = await repo.fetch_scribe_batch(batch_size=10)
for v in videos:
    transcript = await extract_transcript(v.id)
    await repo.mark_transcript_safe(v.id)
```

## Configuration

See [ENV.example](ENV.example) for all options.

## License

MIT
