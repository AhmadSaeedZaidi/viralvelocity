"""Maia Muralist: full-video archival consumer ("super painter").

Consumer in the Producer-Consumer pipeline. For every video that has not yet had
its source clip archived, it downloads the video at a compact native resolution
(YouTube's own pre-encoded stream — **no CPU re-encode**) via the shared
:class:`~maia.media.streamer.StealthVideoStreamer` and writes it to the vault at
``videos/{video_id}.mp4`` (batched into a single commit), then marks
``has_video``.

**Status: manual-only.** There is deliberately *no* systemd unit for the muralist
— it is not part of the polling Producer/Consumer loop. Archiving full source
clips is storage-hungry (~80–150 MB per 5–10 min clip at 720p; ~13–17 MB at
360p) and was historically avoided until the project could obtain HF storage.
The muralist is kept as a runnable, claim-based capability so it can be switched
on (fleet-scheduled) once storage allows. Run it manually:

    python -m maia muralist                       # archive most recent un-archived video
    python -m maia muralist --batch-size 10       # archive a batch
    python -m maia muralist --height 480          # choose resolution
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
from atlas.vault import get_vault, video_path
from prefect import flow, get_run_logger, task

from maia.media.streamer import StealthVideoStreamer, VideoExtractionError
from maia.utils import notify_quota_exhausted, vault_op_with_retry

logger = logging.getLogger(__name__)

# Default archival resolution. 720p keeps good visual fidelity while still using
# YouTube's pre-encoded stream (no local transcode). Drop to 360/480 to hoard
# more videos per TB.
DEFAULT_HEIGHT = 720

# Archival is bandwidth/heavy; keep concurrency low to avoid YouTube HTTP 429.
MAX_CONCURRENT_VIDEOS = 2

# Pacing delay (seconds) between video downloads.
MURALIST_THROTTLE_SECONDS = 2.0


@task(name="fetch_muralist_targets")
async def fetch_muralist_targets_task(batch_size: int) -> list[Video]:
    """Fetch videos that still need their source clip archived."""
    video_repo = VideoRepository()
    targets = await video_repo.claim_muralist_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing video archival.")
    return targets  # type: ignore[no-any-return]


@task(name="process_video")
async def process_video_task(video: Video) -> tuple[str, bytes, str] | None:
    """Download *video*'s source clip and return ``(video_id, bytes, ext)``.

    Storage is deferred to the flow level so an entire batch of clips can be
    written to the vault in ONE commit, keeping us under HuggingFace's
    128-commits/hour cap. Returns ``None`` on failure.

    ``QuotaExhaustedError`` is re-raised so the flow can apply backoff / retry.
    """
    video_repo = VideoRepository()
    run_logger = get_run_logger()
    vid_id = video.id

    tmpdir = tempfile.mkdtemp(prefix="muralist-")
    try:
        streamer = StealthVideoStreamer()
        video_path_local = await asyncio.to_thread(
            streamer.extract_video, vid_id, tmpdir, DEFAULT_HEIGHT
        )
        video_bytes = await asyncio.to_thread(video_path_local.read_bytes)
        run_logger.info(
            f"Downloaded {vid_id} → {len(video_bytes) / 1048576:.1f} MB "
            f"({video_path_local.suffix})"
        )
        return (vid_id, video_bytes, video_path_local.suffix.lstrip("."))
    except QuotaExhaustedError:
        await notify_quota_exhausted("muralist")
        raise
    except VideoExtractionError as e:
        # Likely transient (throttle / network) — release to PENDING for retry
        # rather than marking FAILED, so a bad hour doesn't strand the video.
        run_logger.warning(f"Video extraction failed for {vid_id}, releasing: {e}")
        await video_repo.release_to_pending(vid_id)
        return None
    except Exception as e:
        run_logger.exception(f"Muralist failed on {vid_id}: {e}")
        await video_repo.mark_failed(vid_id)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@flow(name="run_muralist_cycle")
async def muralist_flow(batch_size: int, height: int = DEFAULT_HEIGHT) -> dict[str, Any]:
    """Execute a Muralist cycle: archive source clips to the vault."""
    run_logger = get_run_logger()
    run_logger.info(f"=== Starting Muralist Cycle (<= {height}p) ===")

    targets = await fetch_muralist_targets_task(batch_size)

    if not targets:
        run_logger.info("No videos need video archival. Muralist cycle complete (idle).")
        return {"videos_processed": 0}

    run_logger.info(f"Processing {len(targets)} videos concurrently...")

    sem = asyncio.Semaphore(MAX_CONCURRENT_VIDEOS)

    async def _bounded(video: Video) -> tuple[str, bytes, str] | None:
        async with sem:
            result = await process_video_task(video)
            await asyncio.sleep(MURALIST_THROTTLE_SECONDS)
            return result

    results = await asyncio.gather(
        *[_bounded(v) for v in targets], return_exceptions=True
    )
    # Propagate any QuotaExhaustedError that was caught by return_exceptions.
    quota_errors = [r for r in results if isinstance(r, QuotaExhaustedError)]
    if quota_errors:
        raise quota_errors[0]

    extracted: list[tuple[str, bytes, str]] = [r for r in results if isinstance(r, tuple)]

    if extracted:
        video_repo = VideoRepository()
        v = get_vault()
        items = [
            (video_path(vid, ext), io.BytesIO(b)) for vid, b, ext in extracted
        ]
        try:
            await vault_op_with_retry(lambda: v.store_batch(items))  # type: ignore[arg-type]
            for vid, _, _ in extracted:
                await video_repo.mark_video_safe(vid)
            run_logger.info(
                f"Batched {len(extracted)} source clips into ONE vault commit"
            )
        except Exception as e:
            run_logger.exception(f"Batched video store failed ({len(extracted)} vids): {e}")
            for vid, _, _ in extracted:
                await video_repo.mark_failed(vid)

    # Cycle completed without quota exhaustion — clear any stale marker.
    clear_quota_exhausted("muralist")

    run_logger.info(f"=== Muralist Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class MuralistAgent:
    """Muralist Agent: full-video archival ("super painter"). Manual-only."""

    name = "muralist"

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
        parser.add_argument(
            "--height",
            type=int,
            default=DEFAULT_HEIGHT,
            help="Max resolution to archive (default: 720)",
        )

    async def run(
        self, batch_size: int = 5, height: int = DEFAULT_HEIGHT, **kwargs: Any
    ) -> dict[str, Any]:
        return await muralist_flow(batch_size=batch_size, height=height)


def main() -> None:
    try:
        agent = MuralistAgent()
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Muralist stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Muralist failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
