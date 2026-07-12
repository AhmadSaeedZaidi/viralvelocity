"""Maia Streamer: YouTube source fetcher (network pull only).

FIRST half of the streamer/singer split. The streamer does the network-heavy,
rate-limit-prone YouTube download of a video's best audio stream and stores it
to the vault as a raw artifact at ``raw/{id}.{ext}``, then flags the video
``fetched``. It does NOT extract audio or call any STT API.

The **singer** consumer later fetches that raw artifact and runs ffmpeg
locally to extract the speech track into ``audio/{id}.opus`` — keeping the
YouTube rate-limit surface confined to this single agent.

Keeping the YouTube fetch here — and only here — means each video is pulled
from YouTube exactly once for its speech track, instead of the Scribe
re-downloading audio per video (which is what triggered per-IP 429s before).
"""

import argparse
import asyncio
import io
import logging
import shutil
import tempfile
from typing import Any

from atlas.models import Video
from atlas.repositories import VideoRepository
from atlas.state import clear_quota_exhausted
from atlas.utils import QuotaExhaustedError
from atlas.vault import get_vault, meta_path
from maia.media.streamer import (
    AudioExtractionError,
    StealthVideoStreamer,
    StreamRateLimitError,
)
from maia.utils import notify_quota_exhausted, vault_op_with_retry
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

# The YouTube fetch is bandwidth/heavy and rate-limit prone; keep concurrency low
# to avoid HTTP 429 on the (flagged) VPS egress IP.
MAX_CONCURRENT_VIDEOS = 2

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

    Calls the shared streamer's single ingress (``download_unified``) which
    pulls the audio + metadata (incl. stream URLs) in ONE YouTube session.
    Captions are intentionally NOT fetched here — that is owned by the Scribe.
    Storage is deferred to the flow level so an entire batch is written to the
    vault in ONE commit, keeping us under HuggingFace's 128-commits/hour cap.
    Returns ``None`` on failure (the video has already been released/marked).

    ``QuotaExhaustedError`` and ``StreamRateLimitError`` are re-raised so the
    flow can apply backoff / retry policy.
    """

    video_repo = VideoRepository()
    run_logger = get_run_logger()
    vid_id = video.id

    # Idempotent: a video whose raw is already fetched is never re-pulled
    # (P1b per-step state). The claim gate already excludes it; this guards
    # manual / out-of-band reruns from a redundant YouTube fetch.
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
        await notify_quota_exhausted("streamer")
        raise
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
    run_logger = get_run_logger()
    run_logger.info("=== Starting Streamer Cycle ===")

    targets = await fetch_streamer_targets_task(batch_size)

    if not targets:
        run_logger.info("No videos need a fetch. Streamer cycle complete (idle).")
        return {"videos_processed": 0}

    run_logger.info(f"Processing {len(targets)} videos concurrently...")

    sem = asyncio.Semaphore(MAX_CONCURRENT_VIDEOS)

    async def _bounded(video: Video) -> Any:
        async with sem:
            result = await fetch_source_task(video)
            await asyncio.sleep(STREAMER_THROTTLE_SECONDS)
            return result

    results = await asyncio.gather(*[_bounded(v) for v in targets], return_exceptions=True)
    # Propagate any QuotaExhaustedError that was caught by return_exceptions.
    quota_errors = [r for r in results if isinstance(r, QuotaExhaustedError)]
    if quota_errors:
        raise quota_errors[0]

    fetched = [
        r
        for r in results
        if isinstance(r, tuple) and len(r) == 4  # (id, raw_uri, raw_bytes, meta_bytes)
    ]

    if fetched:
        video_repo = VideoRepository()
        v = get_vault()
        items: list[tuple[str, io.BytesIO]] = []
        for _id, raw_uri, raw_bytes, _meta in fetched:
            items.append((raw_uri, io.BytesIO(raw_bytes)))
            if _meta:
                items.append((meta_path(_id), io.BytesIO(_meta)))
        try:
            await vault_op_with_retry(lambda: v.store_batch(items))  # type: ignore[arg-type]
            for _id, raw_uri, _rb, _mb in fetched:
                await video_repo.mark_fetched(_id, raw_uri)
            run_logger.info(f"Batched {len(fetched)} videos' raw+meta into ONE vault commit")
        except Exception as e:
            run_logger.exception(f"Batched unified store failed ({len(fetched)} vids): {e}")
            for _id, *_ in fetched:
                await video_repo.mark_failed(_id)

    # Cycle completed without quota exhaustion — clear any stale marker.
    clear_quota_exhausted("streamer")

    run_logger.info(f"=== Streamer Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class StreamerAgent:
    """Streamer Agent: fetch YouTube sources for the singer to extract."""

    name = "streamer"

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5,
            help="Number of videos to process per cycle (default: 5)",
        )

    async def run(self, batch_size: int = 5, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = await streamer_flow(batch_size=batch_size)
        return result


def main() -> None:
    try:
        agent = StreamerAgent()
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Streamer stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Streamer failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
