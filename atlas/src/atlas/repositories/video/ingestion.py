import logging
import re
from typing import Any

from atlas.adapters import DatabaseAdapter
from atlas.models.video import Video, VideoStats

logger = logging.getLogger("atlas.repositories.video.ingestion")

_ARCHIVAL_BATCH_SIZE = 100

_ISO_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _parse_iso_duration(value: Any) -> int | None:
    """Parse an ISO-8601 duration (``PT1M5S``) to seconds; None if absent/invalid."""
    if not isinstance(value, str):
        return None
    m = _ISO_DURATION_RE.match(value)
    if not m:
        return None
    d, h, mn, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mn * 60 + s


class VideoIngestionMixin(DatabaseAdapter):
    async def save(self, video: Video) -> None:
        query = """
            INSERT INTO videos (
                id, channel_id, title, published_at, duration,
                tags, category_id, default_language, wiki_topics,
                discovered_at, last_updated_at, status,
                has_transcript, has_visuals
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                channel_id = COALESCE(EXCLUDED.channel_id, videos.channel_id),
                title = EXCLUDED.title,
                published_at = COALESCE(EXCLUDED.published_at, videos.published_at),
                tags = COALESCE(EXCLUDED.tags, videos.tags),
                category_id = COALESCE(EXCLUDED.category_id, videos.category_id),
                -- PENDING is the model default; treat it as "no explicit status
                -- change" so a re-save of an already-PROCESSED/ARCHIVED video does
                -- not regress its lifecycle state. An explicit non-PENDING status
                -- is always honoured.
                status = COALESCE(NULLIF(EXCLUDED.status, 'PENDING'), videos.status)
        """
        await self._execute(
            query,
            (
                video.id,
                video.channel_id,
                video.title,
                video.published_at,
                video.duration,
                video.tags,
                video.category_id,
                video.default_language,
                video.wiki_topics,
                video.discovered_at,
                video.last_updated_at,
                video.status,
                video.has_transcript,
                video.has_visuals,
            ),
        )

    async def get_by_id(self, video_id: str) -> Video | None:
        row = await self._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))
        return Video.model_validate(row) if row else None

    async def get_latest_stats(self, video_id: str) -> VideoStats | None:
        row = await self._fetch_one(
            """
            SELECT video_id, views, likes, comment_count, timestamp
            FROM video_stats_log
            WHERE video_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (video_id,),
        )
        return VideoStats.model_validate(row) if row else None

    async def ingest_video_metadata(
        self, video_data: dict[str, Any], priority_override: int | None = None
    ) -> None:
        from datetime import UTC, datetime

        snippet = video_data.get("snippet", {})
        channel_id = snippet.get("channelId")
        channel_title = (
            snippet.get("channelTitle")
            or snippet.get("channel")
            or (_CHANNEL_TITLE_PENDING if channel_id else None)
        )

        async with self._connection() as conn:
            if channel_id and channel_title:
                channel_upsert = """
                    INSERT INTO channels (id, title, created_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = CASE
                            WHEN BTRIM(EXCLUDED.title) <> ''
                                AND EXCLUDED.title <> %s
                            THEN EXCLUDED.title
                            WHEN BTRIM(channels.title) <> ''
                                AND channels.title <> %s
                            THEN channels.title
                            ELSE EXCLUDED.title
                        END
                """
                pend = _CHANNEL_TITLE_PENDING
                await conn.execute(
                    channel_upsert,
                    (channel_id, channel_title, datetime.now(UTC), pend, pend),
                )

            video_query = """
                INSERT INTO videos (
                    id, channel_id, title, published_at, duration,
                    tags, category_id, default_language, discovered_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    duration = COALESCE(EXCLUDED.duration, videos.duration)
            """
            vid_id = video_data.get("id")
            if isinstance(vid_id, dict):
                vid_id = vid_id.get("videoId")

            if not vid_id:
                await conn.commit()
                return

            # Duration only comes from contentDetails, present when enriched via
            # videos.list (search snippets omit it).
            duration = _parse_iso_duration(video_data.get("contentDetails", {}).get("duration"))

            await conn.execute(
                video_query,
                (
                    vid_id,
                    channel_id,
                    snippet.get("title"),
                    snippet.get("publishedAt"),
                    duration,
                    snippet.get("tags", []),
                    snippet.get("categoryId"),
                    snippet.get("defaultLanguage"),
                    datetime.now(UTC),
                ),
            )
            await conn.commit()


_CHANNEL_TITLE_PENDING = "Pending channel index"
