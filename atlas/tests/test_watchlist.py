"""Unit tests for adaptive-scheduling decay tier logic (no DB)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from atlas.repositories import WatchlistRepository
from atlas.repositories.watchlist import WatchlistRepository as WatchlistRepo


@pytest.fixture
def repo() -> WatchlistRepository:
    """WatchlistRepository with the default config thresholds."""
    return WatchlistRepository(db_pool=None)  # no DB access in these tests


class FakeDriver:
    """Fake DB driver capturing the SQL executed and returning canned rows.

    ``_fetch_all`` returns ``velocity_rows`` in the exact order the mock wants
    (simulating the windowed query already ordered by timestamp DESC), and
    records the params of the last query for FIFO/cap assertions.
    """

    def __init__(self, fetch_all_rows: list[dict[str, Any]] | None = None) -> None:
        self.fetch_all_rows = fetch_all_rows or []
        self.last_params: tuple[Any, ...] | None = None
        self.execute_many_calls: list[tuple[Any, ...]] = []

    async def _fetch_all(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        self.last_params = params
        return self.fetch_all_rows

    async def _execute_many(
        self, query: str, params_list: list[tuple[Any, ...]]
    ) -> None:
        self.execute_many_calls = params_list


def _repo_with(fake: FakeDriver) -> WatchlistRepo:
    """Build a WatchlistRepository bound to the fake driver."""
    repo = WatchlistRepo(db_pool=None)
    repo._fetch_all = fake._fetch_all  # type: ignore[method-assign]
    repo._execute_many = fake._execute_many  # type: ignore[method-assign]
    return repo


def _vt(views: int | None, ts: datetime) -> dict[str, Any]:
    return {"video_id": "V1", "views": views, "timestamp": ts}


@pytest.mark.parametrize(
    "age, expected_tier",
    [
        (timedelta(hours=2), "HOURLY"),
        (timedelta(hours=23), "HOURLY"),
        (timedelta(hours=24), "DAILY"),
        (timedelta(days=3), "DAILY"),
        (timedelta(days=6, hours=23), "DAILY"),
        (timedelta(days=7), "WEEKLY"),
        (timedelta(days=30), "WEEKLY"),
    ],
)
def test_age_floor(repo, age, expected_tier):
    published = datetime.now(UTC) - age
    tier, _next = repo.calculate_next_track_time(published_at=published)
    assert tier == expected_tier


def test_hot_velocity_promotes_old_video(repo):
    """A viral old video (velocity >= HOT) stays HOURLY past the age cutoffs."""
    published = datetime.now(UTC) - timedelta(days=30)
    tier, next_at = repo.calculate_next_track_time(
        published_at=published, views_per_hour=500.0
    )
    assert tier == "HOURLY"
    assert next_at <= datetime.now(UTC) + timedelta(hours=1, minutes=1)


def test_dead_velocity_drops_weekly_floor(repo):
    """A video with essentially no views/hour drops one tier, flooring at WEEKLY."""
    published = datetime.now(UTC) - timedelta(days=2)  # DAILY by age
    tier, next_at = repo.calculate_next_track_time(
        published_at=published, views_per_hour=0.0
    )
    assert tier == "WEEKLY"
    assert next_at <= datetime.now(UTC) + timedelta(days=7, hours=1)


def test_dead_hourly_drops_to_daily(repo):
    """A <24h video with zero velocity drops to DAILY (never slower than weekly)."""
    published = datetime.now(UTC) - timedelta(hours=10)  # HOURLY by age
    tier, next_at = repo.calculate_next_track_time(
        published_at=published, views_per_hour=0.0
    )
    assert tier == "DAILY"


def test_unknown_velocity_keeps_age_floor(repo):
    """Unknown velocity (None) falls back to the age floor, no boost or drop."""
    published = datetime.now(UTC) - timedelta(days=3)  # DAILY
    tier, _ = repo.calculate_next_track_time(published_at=published, views_per_hour=None)
    assert tier == "DAILY"


def test_no_published_at_uses_current_tier(repo):
    """After janitor deletes the videos row, published_at is NULL: keep current tier."""
    tier, next_at = repo.calculate_next_track_time(
        published_at=None, views_per_hour=None, tier="WEEKLY"
    )
    assert tier == "WEEKLY"
    assert next_at > datetime.now(UTC)


# --- Velocity SQL (mocked/fake driver, never the live DB) ---


@pytest.mark.asyncio
async def test_velocity_views_per_hour_math_reports_units():
    """Two samples 2h apart growing +50 views -> 25 views/hour (units of views/h)."""
    now = datetime.now(UTC)
    fake = FakeDriver(
        fetch_all_rows=[
            _vt(150, now),  # latest first (windowed query DESC)
            _vt(100, now - timedelta(hours=2)),
        ]
    )
    repo = _repo_with(fake)
    result = await repo.velocity_views_per_hour(["V1"])
    assert result == {"V1": 25.0}


@pytest.mark.asyncio
async def test_velocity_passes_limit_tuple_to_db():
    """The velocity query is parameterised by the requested video_ids and uses
    ANY(%s) — the fake driver receives them verbatim no quoting assumptions."""
    from datetime import UTC

    now = datetime.now(UTC)
    fake = FakeDriver(
        fetch_all_rows=[
            _vt(10, now),
            _vt(5, now - timedelta(hours=1)),
        ]
    )
    repo = _repo_with(fake)
    await repo.velocity_views_per_hour(["V1"])
    assert fake.last_params == (["V1"],)


@pytest.mark.asyncio
async def test_velocity_ignores_rows_for_other_videos():
    """Each video is computed from its own rows only; shared ids don't leak."""
    now = datetime.now(UTC)
    fake = FakeDriver(
        fetch_all_rows=[
            {"video_id": "V1", "views": 0, "timestamp": now},
            {"video_id": "V1", "views": 0, "timestamp": now - timedelta(hours=1)},
            {"video_id": "V2", "views": 100, "timestamp": now},
            {"video_id": "V2", "views": 50, "timestamp": now - timedelta(hours=5)},
        ]
    )
    repo = _repo_with(fake)
    result = await repo.velocity_views_per_hour(["V1", "V2"])
    assert result == {"V1": 0.0, "V2": 10.0}


@pytest.mark.asyncio
async def test_velocity_single_sample_is_unknown():
    """Fewer than two samples for a video -> None (unknown velocity)."""
    now = datetime.now(UTC)
    fake = FakeDriver(fetch_all_rows=[_vt(100, now)])
    repo = _repo_with(fake)
    assert await repo.velocity_views_per_hour(["V1"]) == {"V1": None}


@pytest.mark.asyncio
async def test_velocity_non_positive_time_delta_is_unknown():
    """A zero/negative elapsed window (clock skew / retry) -> None, not a div by zero."""
    now = datetime.now(UTC)
    fake = FakeDriver(fetch_all_rows=[_vt(200, now), _vt(120, now)])
    repo = _repo_with(fake)
    assert await repo.velocity_views_per_hour(["V1"]) == {"V1": None}


@pytest.mark.asyncio
async def test_velocity_null_views_are_unknown():
    """Missing views on either sample -> None (can't measure)."""
    now = datetime.now(UTC)
    fake = FakeDriver(fetch_all_rows=[_vt(None, now), _vt(100, now - timedelta(hours=1))])
    repo = _repo_with(fake)
    assert await repo.velocity_views_per_hour(["V1"]) == {"V1": None}


@pytest.mark.asyncio
async def test_velocity_empty_ids_short_circuits():
    """No ids requested -> {} without hitting the DB (no _fetch_all call)."""
    fake = FakeDriver()
    repo = _repo_with(fake)
    assert await repo.velocity_views_per_hour([]) == {}
    assert fake.last_params is None


# --- fetch_batch FIFO + batch cap ---


def _wl_row(video_id: str, next_at: datetime) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "tracking_tier": "HOURLY",
        "last_tracked_at": None,
        "next_track_at": next_at,
        "created_at": None,
        "published_at": None,
    }


@pytest.mark.asyncio
async def test_fetch_batch_respects_fifo_order():
    """Rows come back in next_track_at ASC order and are parsed in that order."""
    now = datetime.now(UTC)
    fake = FakeDriver(
        fetch_all_rows=[
            _wl_row("V1", now + timedelta(minutes=5)),
            _wl_row("V2", now + timedelta(minutes=1)),
            _wl_row("V3", now + timedelta(minutes=3)),
        ]
    )
    repo = _repo_with(fake)
    items = await repo.fetch_batch(batch_size=50)
    assert [i.video_id for i in items] == ["V1", "V2", "V3"]


@pytest.mark.asyncio
async def test_fetch_batch_caps_to_limit():
    """batch_size is forwarded as the LIMIT param and bounds the returned batch."""
    fake = FakeDriver(fetch_all_rows=[_wl_row(f"V{i}", datetime.now(UTC)) for i in range(10)])
    repo = _repo_with(fake)
    items = await repo.fetch_batch(batch_size=10)
    assert fake.last_params == (10,)
    assert len(items) == 10


@pytest.mark.asyncio
async def test_fetch_batch_empty_is_empty():
    fake = FakeDriver(fetch_all_rows=[])
    repo = _repo_with(fake)
    assert await repo.fetch_batch(batch_size=10) == []


# --- update_schedule ---


@pytest.mark.asyncio
async def test_update_schedule_writes_params_in_column_order():
    """Each update becomes a row of (tier, last_tracked_at, next_track_at, video_id)."""
    fake = FakeDriver()
    repo = _repo_with(fake)
    now = datetime.now(UTC)
    updates = [
        {
            "video_id": "V1",
            "tracking_tier": "DAILY",
            "last_tracked_at": now,
            "next_track_at": now + timedelta(days=1),
        }
    ]
    await repo.update_schedule(updates)
    params = fake.execute_many_calls[0]
    assert params == ("DAILY", now, now + timedelta(days=1), "V1")


@pytest.mark.asyncio
async def test_update_schedule_empty_short_circuits():
    """No updates -> _execute_many is never called."""
    fake = FakeDriver()
    repo = _repo_with(fake)
    await repo.update_schedule([])
    assert fake.execute_many_calls == []
