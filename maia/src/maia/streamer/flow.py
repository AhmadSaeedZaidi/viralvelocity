"""Maia Streamer: YouTube source fetcher (network pull only).

The streamer does the network-heavy, rate-limit-prone YouTube download of a
video's best audio stream and stores it to the vault as a raw artifact, then
flags the video ``fetched``. The singer later extracts speech locally, keeping
the YouTube rate-limit surface confined to this single agent.
"""

import asyncio
import logging
import shutil
import tempfile
from typing import Any

from atlas.models import Video
from atlas.repositories import VideoRepository
from atlas.utils import QuotaExhaustedError
from atlas.vault import get_vault, meta_path
from maia.base import BaseBatchAgent
from maia.media.streamer import (
    AudioExtractionError,
    StealthVideoStreamer,
    StreamRateLimitError,
)
from maia.storage import commit_artifacts
from maia.utils import cli_bootstrap, notify_quota_exhausted, run_agent_main, vault_op_with_retry
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

# The YouTube fetch is bandwidth/heavy and rate-limit prone; keep concurrency low
# to avoid HTTP 429 on the (flagged) VPS egress IP.
MAX_CONCURRENT_VIDEOS = 1

# Pacing delay (seconds) between fetches.
STREAMER_THROTTLE_SECONDS = 1.5


@task(name="fetch_streamer_targets")
async def fetch_streamer_targets_task(batch_size: int) -> list[Video]:
    """Fetch videos whose YouTube source has not yet been fetched."""
    video_repo = VideoRepository()
    targets = await video_repo.claim_streamer_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing a source fetch.")
    return targets  # type: ignore[no-any-return]


@task(name="fetch_source")
async def fetch_source_task(
    video: Video,
) -> tuple[str, str, bytes, bytes | None] | None:
    """Unified YouTube fetch for *video*; return ``(id, raw_uri, raw_bytes, meta_bytes)``.

    Calls the shared streamer's single ingress (``download_unified``) which pulls
    the audio + metadata in ONE YouTube session. Captions are NOT fetched here
    (owned by the Scribe); storage is deferred to the flow level so a batch is
    written in ONE commit. Returns ``None`` on failure.
    """

    video_repo = VideoRepository()
    run_logger = get_run_logger()
    vid_id = video.id

    # Idempotent: a DONE video is never re-pulled; this guards manual reruns
    # from a redundant YouTube fetch (the claim gate already excludes it).
    if video.raw_phase == "DONE":
        run_logger.info(f"Raw already fetched for {vid_id} — skipping")
        return None

    tmpdir = tempfile.mkdtemp(prefix="streamer-raw-")
    try:
        streamer = StealthVideoStreamer()
        audio_path, info_file = await asyncio.to_thread(streamer.download_unified, vid_id, tmpdir)
        raw_bytes = await asyncio.to_thread(audio_path.read_bytes)
        raw_uri = f"raw/{audio_path.name}"
        run_logger.info(f"Fetched {len(raw_bytes)} bytes of audio for {vid_id}")

        meta_bytes = None
        if info_file is not None:
            meta_bytes = await asyncio.to_thread(info_file.read_bytes)

        return (vid_id, raw_uri, raw_bytes, meta_bytes)
    except QuotaExhaustedError:
        # Release to PENDING for a later cycle instead of re-raising (which would
        # crash the whole service and crash-loop on the auto-restart).
        await notify_quota_exhausted("streamer")
        await video_repo.release_to_pending(vid_id)
        return None
    except StreamRateLimitError as e:
        # Transient — release back to PENDING so it retries on a later cycle.
        run_logger.warning(f"Rate-limited on {vid_id}, releasing: {e}")
        await video_repo.release_to_pending(vid_id)
        return None
    except AudioExtractionError as e:
        # Likely transient (throttle / network) — release to PENDING for retry
        # rather than marking FAILED, so a bad hour doesn't strand the video.
        run_logger.warning(f"Raw fetch failed for {vid_id}, releasing: {e}")
        await video_repo.release_to_pending(vid_id)
        return None
    except Exception as e:
        run_logger.exception(f"Streamer failed on {vid_id}: {e}")
        await video_repo.mark_failed(vid_id)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@flow(name="run_streamer_cycle")
async def streamer_flow(batch_size: int) -> dict[str, Any]:
    """Execute a complete Streamer cycle: fetch raw sources and store to vault."""
    return await StreamerAgent().run(batch_size=batch_size)


class StreamerAgent(BaseBatchAgent):
    """Streamer Agent: fetch YouTube sources for the singer to extract."""

    name = "streamer"
    default_batch_size = 5
    max_concurrent = MAX_CONCURRENT_VIDEOS
    throttle_seconds = STREAMER_THROTTLE_SECONDS

    async def claim_batch(self, n: int) -> list[Video]:
        return await fetch_streamer_targets_task(n)

    async def process_one(self, video: Video) -> tuple[str, str, bytes, bytes | None] | None:
        return await fetch_source_task(video)

    async def store_results(self, results: list[Any]) -> None:
        fetched = [r for r in results if isinstance(r, tuple) and len(r) == 4]
        if not fetched:
            return
        items: list[tuple[str, bytes]] = []
        vids: list[str] = []
        id_uri: dict[str, str] = {}
        for _id, raw_uri, raw_bytes, meta in fetched:
            items.append((raw_uri, raw_bytes))
            if meta:
                items.append((meta_path(_id), meta))
            vids.append(_id)
            id_uri[_id] = raw_uri

        await commit_artifacts(
            items=items,
            video_ids=vids,
            mark_safe=lambda vid: VideoRepository().mark_fetched(vid, id_uri[vid]),
            on_failure=VideoRepository().mark_failed,
            label="videos' raw+meta",
            store=vault_op_with_retry,
            vault=get_vault,
        )


def main() -> None:
    run_agent_main(lambda: streamer_flow(batch_size=StreamerAgent.default_batch_size), "streamer")


if __name__ == "__main__":
    cli_bootstrap()
    main()
