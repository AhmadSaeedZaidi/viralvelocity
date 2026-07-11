"""Maia Singer: local audio extractor + vault storer (no STT).

SECOND half of the streamer/singer split. For every video the **streamer** has
fetched (``fetched`` TRUE, raw artifact stored at ``raw/{id}.{ext}``) but which
still lacks stored audio, the singer fetches that raw artifact, runs ffmpeg
*locally* to extract the speech track into ``audio/{id}.opus``, stores it to the
vault, and flips ``has_audio``.

Crucially the singer performs NO YouTube network call and NO speech-to-text API
call (Grok/Mistral). Transcription is a separate concern handled by the scribe
(with those providers as STT fallbacks). This keeps all rate-limit-prone work in
the streamer and all paid STT work out of this agent.
"""

import argparse
import asyncio
import io
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from atlas.models import Video
from atlas.repositories import VideoRepository
from atlas.vault import audio_path, get_vault
from maia.media.streamer import AudioExtractionError, extract_audio_ffmpeg
from maia.utils import vault_op_with_retry
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

# Local ffmpeg extraction is CPU bound, not rate limited; modest concurrency.
MAX_CONCURRENT_VIDEOS = 2

# Pacing delay (seconds) between extractions.
SINGER_THROTTLE_SECONDS = 1.5


@task(name="fetch_singer_targets")
async def fetch_singer_targets_task(batch_size: int) -> list[Video]:
    """Fetch videos that have been fetched but have no stored audio yet."""
    video_repo = VideoRepository()
    targets = await video_repo.claim_singer_batch(batch_size)
    if targets:
        get_run_logger().info(f"Fetched {len(targets)} videos needing audio storage.")
    return targets  # type: ignore[no-any-return]


@task(name="store_audio")
async def store_audio_task(video: Video) -> tuple[str, bytes] | None:
    """Extract *video*'s speech track from its raw artifact and return audio.

    Storage is deferred to the flow level so the batch is written to the vault in
    ONE commit. Returns ``None`` on failure (the video has already been marked by
    the handler).
    """
    video_repo = VideoRepository()
    run_logger = get_run_logger()
    vid_id = video.id

    if not video.raw_uri:
        run_logger.error(f"No raw_uri for {vid_id} though fetched=TRUE — marking failed")
        await video_repo.mark_failed(vid_id)
        return None

    v = get_vault()
    raw_buf = await asyncio.to_thread(v.fetch_binary, video.raw_uri)
    if raw_buf is None:
        run_logger.error(f"Raw artifact missing for {vid_id} at {video.raw_uri} — marking failed")
        await video_repo.mark_failed(vid_id)
        return None

    tmpdir = tempfile.mkdtemp(prefix="singer-audio-")
    try:
        raw_path = Path(tmpdir) / Path(video.raw_uri).name
        raw_path.write_bytes(raw_buf.getvalue())

        opus_path = Path(tmpdir) / f"{vid_id}.opus"
        await asyncio.to_thread(extract_audio_ffmpeg, raw_path, opus_path)

        audio_bytes = opus_path.read_bytes()
        run_logger.info(f"Extracted {len(audio_bytes)} bytes of audio for {vid_id}")
        return (vid_id, audio_bytes)
    except AudioExtractionError as e:
        run_logger.exception(f"Audio extraction failed for {vid_id}: {e}")
        await video_repo.mark_failed(vid_id)
        return None
    except Exception as e:
        run_logger.exception(f"Singer failed on {vid_id}: {e}")
        await video_repo.mark_failed(vid_id)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@flow(name="run_singer_cycle")
async def singer_flow(batch_size: int) -> dict[str, Any]:
    """Execute a complete Singer cycle: extract + store audio for fetched videos."""
    run_logger = get_run_logger()
    run_logger.info("=== Starting Singer Cycle ===")

    targets = await fetch_singer_targets_task(batch_size)

    if not targets:
        run_logger.info("No videos need audio storage. Singer cycle complete (idle).")
        return {"videos_processed": 0}

    run_logger.info(f"Processing {len(targets)} videos concurrently...")

    sem = asyncio.Semaphore(MAX_CONCURRENT_VIDEOS)

    async def _bounded(video: Video) -> Any:
        async with sem:
            result = await store_audio_task(video)
            await asyncio.sleep(SINGER_THROTTLE_SECONDS)
            return result

    results = await asyncio.gather(*[_bounded(v) for v in targets], return_exceptions=True)

    extracted: list[tuple[str, bytes]] = [r for r in results if isinstance(r, tuple)]

    if extracted:
        video_repo = VideoRepository()
        v = get_vault()
        items = [(audio_path(vid), io.BytesIO(b)) for vid, b in extracted]
        try:
            await vault_op_with_retry(lambda: v.store_batch(items))  # type: ignore[arg-type]
            for vid_id, _ in extracted:
                await video_repo.mark_audio_safe(vid_id)
            # The raw artifact is shared with the painter (frames). Reclaim it
            # only once BOTH consumers have derived their output, so the
            # painter never finds its input gone.
            for vid_id, _ in extracted:
                await video_repo.reclaim_raw_if_complete(vid_id)
            run_logger.info(
                f"Batched {len(extracted)} audio files into ONE vault commit; "
                f"reclaimed raw where frames were also extracted"
            )
        except Exception as e:
            run_logger.exception(f"Batched audio store failed ({len(extracted)} vids): {e}")
            for vid_id, _ in extracted:
                await video_repo.mark_failed(vid_id)

    run_logger.info(f"=== Singer Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class SingerAgent:
    """Singer Agent: extract + store audio (no transcription)."""

    name = "singer"

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of videos to process per cycle (default: 10)",
        )

    async def run(self, batch_size: int = 10, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = await singer_flow(batch_size=batch_size)
        return result


def main() -> None:
    try:
        agent = SingerAgent()
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Singer stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Singer failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
