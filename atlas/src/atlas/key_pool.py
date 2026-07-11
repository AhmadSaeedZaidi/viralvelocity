"""Dynamic API key-pool allocation (quota-math balanced).

The YouTube key pool is split across three rings: ``hunting`` (fresh discovery),
``tracking`` (stats refresh) and ``archeology`` (historical discovery).

Allocation is driven by quota economics, not a fixed bulk/sliver split:

* A ``search.list`` call (hunter) costs **100** quota units; a ``videos.list``
  call (tracker) costs **1** unit. So per key the hunter burns ~100x faster.
* Each ring's share of keys is allocated **proportional to its daily quota
  demand** (operations x unit cost), then clamped by safety floors so no ring is
  starved. Because the hunter is both the heaviest consumer and holds the bulk of
  keys, it is the ring that rate-limits *first* — discovery backs off before the
  tracker, keeping stats fresh. (Invariant: hunter keys always rate-limit before
  tracker keys.)
* ``archeology`` is a fixed reserve (``KEY_POOL_ARCHEOLOGY_SIZE``), floored at 2.

The allocation is recomputed on a slow cadence (weekly) by
:func:`refresh_allocation` and cached to a small JSON override file so that the
frequently-restarting agents read a stable value without hitting the database on
every startup. :func:`load_override` returns ``None`` when no valid cache exists,
in which case the static ``.env`` sizes are used.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("atlas.key_pool")

# Default location of the cached allocation, override with KEY_POOL_ALLOCATION_PATH.
DEFAULT_ALLOCATION_PATH = Path(
    os.environ.get(
        "KEY_POOL_ALLOCATION_PATH",
        str(Path(__file__).resolve().parents[3] / "data" / "pool_allocation.json"),
    )
)

# Recompute cadence: only refresh once the cache is older than this.
REFRESH_INTERVAL_DAYS = 7

# Quota economics (YouTube Data API v3):
#   * videos.list  -> 1 quota unit per call, up to 50 video IDs per call.
#     This is the CHEAPEST call and is what the tracker uses to refresh stats.
#   * search.list  -> 1 quota unit per call but its OWN bucket of 100 calls/day
#     per project. Discovery (the hunter) must use it, so the hunter is the
#     inherent throttle point regardless of key count.
QUOTA_PER_KEY = 10_000
VIDEO_BATCH = 50
VIDEOS_LIST_UNIT_COST = 1
SEARCH_UNIT_COST = 1
SEARCH_BUCKET_PER_KEY = 100  # search.list calls/day per project/key

# Demand proxies (daily operations). Tunable knobs; the point is the RATIO
# driven by quota cost, not the absolute numbers.
TRACKER_REFRESHES_PER_DAY = 4  # full-corpus stats refreshes / day
HUNTER_SEARCHES_PER_DAY = 1_500  # discovery searches / day


@dataclass(frozen=True)
class PoolSizes:
    """Resolved ring sizes for the current key pool."""

    tracking: int
    archeology: int


def tracker_demand_units(
    video_count: int, refreshes_per_day: int = TRACKER_REFRESHES_PER_DAY
) -> int:
    """Daily quota-unit demand for the tracking ring (batched videos.list)."""
    calls = refreshes_per_day * max(1, (video_count + VIDEO_BATCH - 1) // VIDEO_BATCH)
    return calls * VIDEOS_LIST_UNIT_COST


def compute_sizes(
    total_keys: int,
    video_count: int,
    archeology_size: int,
    hunter_searches_per_day: int = HUNTER_SEARCHES_PER_DAY,
) -> PoolSizes:
    """Compute ring sizes from quota math, balanced so no ring is starved.

    Keys are allocated proportional to each ring's daily quota demand
    (operations x unit cost), then clamped by safety floors:

    * ``archeology`` is a fixed reserve, floored at 2 (never starved).
    * ``tracking`` is the cheap ring (videos.list, 1 unit / <=50 ids), so its
      raw demand is tiny; a floor keeps it from ever being the first ring to
      exhaust.
    * ``hunting`` keeps the bulk (and the 100/day search bucket makes it the
      natural point that rate-limits *first*), with a floor so it is not
      starved either.

    Args:
        total_keys: Number of keys in the pool.
        video_count: Current number of videos in the corpus.
        archeology_size: Desired fixed reserve for the archeology ring.
        hunter_searches_per_day: Hunter daily demand proxy (tunable).

    Returns:
        :class:`PoolSizes` with clamped, non-overlapping sizes.
    """
    # Archeology reserve — never starve it.
    archeology = max(2, min(archeology_size, max(0, total_keys - 4)))
    remaining = total_keys - archeology

    # Quota-math daily demand (units) for each ring.
    tracker_u = tracker_demand_units(video_count)
    hunter_u = hunter_searches_per_day * SEARCH_UNIT_COST
    total_u = tracker_u + hunter_u

    # Allocate keys proportional to quota demand...
    tracking = round(remaining * tracker_u / total_u)
    hunting = remaining - tracking

    # ...then apply safety floors so no ring is starved and the cheap tracker
    # ring is never the first to exhaust.
    tracking_floor = max(8, (tracker_u + QUOTA_PER_KEY - 1) // QUOTA_PER_KEY + 4)
    hunting_floor = max(4, remaining // 3)
    if tracking < tracking_floor:
        tracking = tracking_floor
        hunting = remaining - tracking
    if hunting < hunting_floor:
        hunting = hunting_floor
        tracking = max(1, remaining - hunting)
    if tracking + hunting > remaining:
        # Floors can't both fit in a tiny pool — give each at least one key.
        tracking = max(1, remaining - 1)
        hunting = remaining - tracking

    return PoolSizes(tracking=tracking, archeology=archeology)


def load_override(path: Path = DEFAULT_ALLOCATION_PATH) -> PoolSizes | None:
    """Load cached ring sizes, or ``None`` if absent/invalid."""
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return PoolSizes(
            tracking=int(data["tracking"]),
            archeology=int(data["archeology"]),
        )
    except Exception as e:  # noqa: BLE001 - a bad cache must never break startup
        logger.warning(f"Ignoring invalid pool-allocation cache at {path}: {e}")
        return None


def _cache_age_days(path: Path) -> float | None:
    try:
        data = json.loads(path.read_text())
        computed_at = datetime.fromisoformat(data["computed_at"])
        return (datetime.now(UTC) - computed_at).total_seconds() / 86400.0
    except Exception:  # noqa: BLE001
        return None


def refresh_allocation(
    total_keys: int,
    video_count: int,
    archeology_size: int,
    path: Path = DEFAULT_ALLOCATION_PATH,
    force: bool = False,
) -> PoolSizes | None:
    """Recompute and cache ring sizes if the cache is stale (or ``force``).

    Returns the freshly-written :class:`PoolSizes`, or ``None`` if the cache was
    still fresh and nothing was written.
    """
    if not force:
        age = _cache_age_days(path)
        if age is not None and age < REFRESH_INTERVAL_DAYS:
            return None

    sizes = compute_sizes(total_keys, video_count, archeology_size)
    payload = {
        "tracking": sizes.tracking,
        "archeology": sizes.archeology,
        "video_count": video_count,
        "total_keys": total_keys,
        "computed_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    logger.info(
        f"Key-pool allocation refreshed: tracking={sizes.tracking}, "
        f"archeology={sizes.archeology}, hunting={total_keys - sizes.tracking - sizes.archeology} "
        f"(video_count={video_count})"
    )
    return sizes
