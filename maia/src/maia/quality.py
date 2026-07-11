"""Heuristic quality gate for discovered videos (pre-ingestion).

Rejects low-value videos *before* they hit the database or seed the snowball,
using signals from a ``videos.list`` response (``contentDetails`` + ``statistics``)
plus two signals the Data API does **not** expose natively:

* **Duration**   — reject Shorts / sub-minute clips.
* **Velocity**   — views per hour since publication (early/lifetime traction).
* **Engagement** — ``(likes + comments) / views`` (filters bot traffic / clickbait).
* **Shorts**     — the API has no ``isShort`` flag, so we HEAD-probe
  ``/shorts/{id}``: a 200 means it is a Short, a 3xx redirect to ``/watch``
  means it is long-form. Cheap (no body) and quota-free.
* **AI-slop**    — no API flag exists, so we scan title/description/tags against a
  configurable denylist of "this video is AI-generated" phrases, and reject
  suspected AI-farm channels via channel-statistics (subscriber floor + upload
  rate proxy from ``channels.list``).

Thresholds are configured on :class:`atlas.config.Settings` so they can be tuned
without code changes. The gate is pure and side-effect free for easy testing.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

# ISO-8601 durations as returned by the YouTube API, e.g. ``PT1M5S``, ``PT1H2M3S``.
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)

# Redirect status codes returned when a Shorts URL points at a long-form video.
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def parse_iso8601_duration(value: str | None) -> int:
    """Parse an ISO-8601 duration (``PT1M5S``) into whole seconds.

    Returns 0 for empty/unparseable input or zero-length durations (e.g. live
    streams reported as ``P0D``), which the duration gate then rejects.
    """
    if not value:
        return 0
    m = _DURATION_RE.match(value)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


@dataclass(frozen=True)
class QualityThresholds:
    """Tunable gate thresholds."""

    min_duration_seconds: int = 65
    min_views_per_hour: float = 5.0
    min_engagement_rate: float = 0.005
    # Videos younger than this are exempt from the velocity check (too fresh to
    # judge traction); duration + engagement still apply.
    velocity_grace_hours: float = 1.0

    # ── Shorts HEAD probe ──────────────────────────────────────────────────
    shorts_head_enabled: bool = True
    shorts_head_max_duration: int = 600
    shorts_head_timeout: float = 5.0
    shorts_head_concurrency: int = 8

    # ── AI-slop denylist (compiled regexes) ─────────────────────────────────
    ai_patterns: tuple[re.Pattern[str], ...] = ()

    # ── Channel-statistics gate ──────────────────────────────────────────────
    min_subscribers: int = 50
    max_videos_per_day: float = 20.0
    max_videos_per_subscriber: float = 5.0

    @classmethod
    def from_settings(cls) -> "QualityThresholds":
        from atlas.config import get_settings

        s = get_settings()
        return cls(
            min_duration_seconds=s.QUALITY_MIN_DURATION_SECONDS,
            min_views_per_hour=s.QUALITY_MIN_VIEWS_PER_HOUR,
            min_engagement_rate=s.QUALITY_MIN_ENGAGEMENT_RATE,
            shorts_head_enabled=s.QUALITY_SHORTS_HEAD_ENABLED,
            shorts_head_max_duration=s.QUALITY_SHORTS_HEAD_MAX_DURATION,
            shorts_head_timeout=s.QUALITY_SHORTS_HEAD_TIMEOUT,
            shorts_head_concurrency=s.QUALITY_SHORTS_HEAD_CONCURRENCY,
            ai_patterns=tuple(re.compile(p, re.IGNORECASE) for p in s.QUALITY_AI_DENYLIST),
            min_subscribers=s.QUALITY_MIN_SUBSCRIBERS,
            max_videos_per_day=s.QUALITY_MAX_VIDEOS_PER_DAY,
            max_videos_per_subscriber=s.QUALITY_MAX_VIDEOS_PER_SUBSCRIBER,
        )


@dataclass(frozen=True)
class QualityResult:
    """Outcome of evaluating one video."""

    passed: bool
    reason: str
    duration_seconds: int = 0
    views: int = 0
    views_per_hour: float = 0.0
    engagement_rate: float = 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def evaluate_video(
    item: dict[str, Any],
    thresholds: QualityThresholds | None = None,
    now: datetime | None = None,
) -> QualityResult:
    """Evaluate a ``videos.list`` item against the quality thresholds.

    Args:
        item: A YouTube ``videos.list`` entry with ``snippet``, ``contentDetails``
            and ``statistics`` parts.
        thresholds: Overrides; defaults to values from settings.
        now: Reference time (defaults to ``datetime.now(UTC)``); injectable for tests.

    Returns:
        A :class:`QualityResult`; ``passed`` is False with a human-readable
        ``reason`` on the first failing check.
    """
    thresholds = thresholds or QualityThresholds()
    now = now or datetime.now(UTC)

    content = item.get("contentDetails", {}) or {}
    stats = item.get("statistics", {}) or {}
    snippet = item.get("snippet", {}) or {}

    duration = parse_iso8601_duration(content.get("duration"))
    views = _to_int(stats.get("viewCount"))
    likes = _to_int(stats.get("likeCount"))
    comments = _to_int(stats.get("commentCount"))

    # ── Duration ──────────────────────────────────────────────────────────
    if duration < thresholds.min_duration_seconds:
        return QualityResult(
            False, f"duration {duration}s < {thresholds.min_duration_seconds}s",
            duration_seconds=duration, views=views,
        )

    # ── AI-slop (title/description/tags denylist) ─────────────────────────
    ai_hit = _matches_ai(item, thresholds.ai_patterns)
    if ai_hit:
        return QualityResult(
            False, f"ai-slop match: {ai_hit}",
            duration_seconds=duration, views=views,
        )

    # ── Velocity (views/hour) ─────────────────────────────────────────────
    hours_since = _hours_since(snippet.get("publishedAt"), now)
    views_per_hour = views / hours_since if hours_since > 0 else float(views)
    if (
        hours_since >= thresholds.velocity_grace_hours
        and views_per_hour < thresholds.min_views_per_hour
    ):
            return QualityResult(
                False,
                f"velocity {views_per_hour:.2f} v/h < {thresholds.min_views_per_hour}",
                duration_seconds=duration, views=views, views_per_hour=views_per_hour,
            )

    # ── Engagement ────────────────────────────────────────────────────────
    engagement = (likes + comments) / views if views > 0 else 0.0
    if engagement < thresholds.min_engagement_rate:
        return QualityResult(
            False,
            f"engagement {engagement:.4f} < {thresholds.min_engagement_rate}",
            duration_seconds=duration, views=views,
            views_per_hour=views_per_hour, engagement_rate=engagement,
        )

    return QualityResult(
        True, "ok", duration_seconds=duration, views=views,
        views_per_hour=views_per_hour, engagement_rate=engagement,
    )


def _video_text(item: dict[str, Any]) -> str:
    """Lower-cased title + description + tags for AI-slop scanning."""
    snippet = item.get("snippet", {}) or {}
    parts = [snippet.get("title") or "", snippet.get("description") or ""]
    tags = snippet.get("tags") or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    return " \n ".join(str(p) for p in parts if p).lower()


def _matches_ai(item: dict[str, Any], patterns: tuple[re.Pattern[str], ...]) -> str | None:
    """Return the matching denylist pattern (or ``None``) for an AI-slop video."""
    if not patterns:
        return None
    text = _video_text(item)
    for pat in patterns:
        if pat.search(text):
            return pat.pattern
    return None


def _vid_id_of(item: dict[str, Any]) -> str | None:
    vid = item.get("id")
    if isinstance(vid, dict):
        return vid.get("videoId")
    return vid


async def is_youtube_short(
    video_id: str,
    *,
    timeout: float,
    session: aiohttp.ClientSession,
) -> bool | None:
    """Probe ``/shorts/{video_id}`` with a HEAD request (no body download).

    Returns ``True`` if the video is a Short (200 OK), ``False`` if it is
    long-form (3xx redirect to ``/watch``), or ``None`` on network/odd errors
    (treated as "unknown" so we do not over-reject).
    """
    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        async with session.head(
            url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status == 200:
                return True
            if resp.status in _REDIRECT_STATUS:
                return False
            return None
    except Exception:
        return None


async def _load_channel_stats(
    channel_ids: list[str], executor: Any | None
) -> dict[str, dict[str, Any]]:
    """Resolve channel statistics for the given channel IDs via ``channels.list``.

    Returns a mapping of ``channel_id -> {subscriber_count, video_count,
    published_at}``. Empty on any failure (the gate then passes the video
    through rather than over-rejecting).
    """
    if not channel_ids or executor is None:
        return {}
    try:
        from atlas.youtube import lookup_channels

        items = await lookup_channels(
            list(channel_ids), parts="snippet,statistics", executor=executor
        )
    except Exception:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for it in items:
        cid = it.get("id")
        if not cid:
            continue
        stats = it.get("statistics", {}) or {}
        out[cid] = {
            "subscriber_count": _to_int(stats.get("subscriberCount")),
            "video_count": _to_int(stats.get("videoCount")),
            "published_at": (it.get("snippet", {}) or {}).get("publishedAt"),
        }
    return out


def _channel_age_days(published_at: str | None, now: datetime) -> float:
    if not published_at:
        return 0.0
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (now - pub).total_seconds() / 86400.0)


def evaluate_channel(
    channel_id: str | None,
    stats: dict[str, Any] | None,
    thresholds: QualityThresholds,
    now: datetime,
) -> tuple[bool, str]:
    """Reject suspected AI-farm / spam channels via statistics.

    * subscriber floor — tiny channels dumping AI-slop.
    * upload-rate proxy — ``videoCount / channel_age_days`` over a threshold
      indicates mass automated uploading.
    """
    if not channel_id or not stats:
        return True, ""
    subs = stats.get("subscriber_count")
    if thresholds.min_subscribers and subs is not None and subs < thresholds.min_subscribers:
        return False, f"channel subs {subs} < {thresholds.min_subscribers}"

    if thresholds.max_videos_per_day and stats.get("video_count"):
        age = _channel_age_days(stats.get("published_at"), now)
        if age > 1.0:  # need a meaningful age to estimate a rate
            rate = stats["video_count"] / age
            if rate > thresholds.max_videos_per_day:
                return (
                    False,
                    f"channel upload rate {rate:.1f}/day > {thresholds.max_videos_per_day}",
                )

    # Subscriber-density signal: far more videos than subscribers is the
    # hallmark of spam/AI farms and is robust to legit high-volume networks
    # (which have many subscribers per video). Normalises volume by audience.
    if thresholds.max_videos_per_subscriber and subs:
        density = stats.get("video_count", 0) / max(subs, 1)
        if density > thresholds.max_videos_per_subscriber:
            return (
                False,
                f"channel videos/sub ratio {density:.1f} > "
                f"{thresholds.max_videos_per_subscriber}",
            )
    return True, ""


async def filter_by_quality(
    search_items: list[dict[str, Any]],
    executor: Any | None,
    enabled: bool | None = None,
    logger: Any | None = None,
    thresholds: QualityThresholds | None = None,
) -> list[dict[str, Any]]:
    """Enrich search results via ``videos.list`` and keep only passing videos.

    Search snippets lack ``statistics``/``contentDetails``, so we resolve them
    (1 quota unit per 50 videos) and apply :func:`evaluate_video`. Returns the
    **enriched** passing items (which carry duration + stats for richer ingest).

    If the gate is disabled or there is no executor to enrich with, the raw
    search items are returned unchanged. ``QuotaExhaustedError`` from the lookup
    propagates to the caller.
    """
    from atlas.config import get_settings
    from atlas.youtube import lookup_videos

    if enabled is None:
        enabled = get_settings().QUALITY_GATE_ENABLED

    ids: list[str] = []
    for it in search_items:
        vid = it.get("id")
        if isinstance(vid, dict):
            vid = vid.get("videoId")
        if vid:
            ids.append(vid)

    if not ids or not enabled or executor is None:
        return search_items

    enriched = await lookup_videos(ids, executor=executor)
    thresholds = QualityThresholds.from_settings()
    now = datetime.now(UTC)

    # ── 1. Per-video signal gate (duration / velocity / engagement / AI) ──
    evaluated: list[tuple[dict[str, Any], QualityResult]] = []
    rejected = 0
    for item in enriched:
        result = evaluate_video(item, thresholds)
        if result.passed:
            evaluated.append((item, result))
        else:
            rejected += 1
            if logger:
                logger.debug(f"Quality gate rejected {_vid_id_of(item)}: {result.reason}")

    # ── 2. Shorts HEAD probe (quota-free, no body download) ──────────────
    if thresholds.shorts_head_enabled and evaluated:
        candidates = [
            (it, res)
            for it, res in evaluated
            if res.duration_seconds < thresholds.shorts_head_max_duration
        ]
        if candidates:
            sem = asyncio.Semaphore(thresholds.shorts_head_concurrency)
            timeout = thresholds.shorts_head_timeout

            async def _probe(
                it: dict[str, Any], res: QualityResult
            ) -> tuple[dict, QualityResult] | None:
                vid = _vid_id_of(it)
                if not vid:
                    return (it, res)
                async with sem:
                    is_short = await is_youtube_short(vid, timeout=timeout, session=session)
                # Unknown (None) is treated as "not a Short" to avoid over-rejecting.
                return None if is_short else (it, res)

            async with aiohttp.ClientSession() as session:
                probed = await asyncio.gather(*(_probe(it, res) for it, res in candidates))
            kept = [p for p in probed if p is not None]
            long_form = [
                (it, res)
                for it, res in evaluated
                if res.duration_seconds >= thresholds.shorts_head_max_duration
            ]
            shorts_hits = len(candidates) - len(kept)
            rejected += shorts_hits
            evaluated = long_form + kept
            if logger and shorts_hits:
                logger.info(f"Quality gate: rejected {shorts_hits} Shorts via HEAD probe")

    # ── 3. Channel-statistics gate (AI-farm / spam) ───────────────────────
    if evaluated:
        channel_ids = list(
            {it.get("snippet", {}).get("channelId") for it, _ in evaluated}
            - {None}
        )
        stats_map = await _load_channel_stats(channel_ids, executor)
        final: list[dict[str, Any]] = []
        for it, _ in evaluated:
            cid = it.get("snippet", {}).get("channelId")
            ok, reason = evaluate_channel(cid, stats_map.get(cid), thresholds, now)
            if ok:
                final.append(it)
            else:
                rejected += 1
                if logger:
                    logger.debug(f"Quality gate rejected {_vid_id_of(it)} (channel): {reason}")
        evaluated = [(it, None) for it in final]

    passing = [it for it, _ in evaluated]
    if logger:
        logger.info(
            f"Quality gate: {len(passing)} passed / {rejected} rejected "
            f"/ {len(enriched)} enriched"
        )
    return passing


def _hours_since(published_at: str | None, now: datetime) -> float:
    if not published_at:
        return 0.0
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (now - pub).total_seconds() / 3600.0)
