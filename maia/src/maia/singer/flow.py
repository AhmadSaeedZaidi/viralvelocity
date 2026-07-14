"""Maia Singer: local audio extractor + vault storer (no STT).

For every video the **streamer** has fetched but which still lacks stored audio,
the singer fetches that raw artifact, runs ffmpeg *locally* to extract the speech
track into ``audio/{id}.opus``, stores it to the vault, and flips ``has_audio``.
The singer performs NO YouTube call and NO speech-to-text API call — transcription
is handled separately by the scribe.
"""

import asyncio
import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from atlas.models import Video
from atlas.repositories import VideoRepository
from atlas.vault import audio_path, get_vault
from maia.base import BaseBatchAgent
from maia.media.streamer import (
    AudioExtractionError,
    extract_audio_chunk,
    extract_audio_ffmpeg,
)
from maia.storage import commit_artifacts
from maia.utils import vault_op_with_retry
from prefect import flow, get_run_logger, task

logger = logging.getLogger(__name__)

# Local ffmpeg extraction is CPU bound, not rate limited; modest concurrency.
MAX_CONCURRENT_VIDEOS = 1

# Long videos are split into chunks of this many seconds so each ffmpeg call
# stays well under the extraction timeout; the full audio track is still
# captured across chunks.
SINGER_CHUNK_SECONDS = 1800

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
async def store_audio_task(video: Video) -> list[tuple[str, str, bytes]] | None:
    """Extract *video*'s speech track from its raw artifact into vault-ready bytes.

    Long videos are split into ``SINGER_CHUNK_SECONDS`` chunks so each ffmpeg
    call stays under the extraction timeout; the full track is captured across
    chunks. Returns a list of ``(video_id, vault_rel_path, audio_bytes)`` for the
    flow level to commit in ONE vault write, or ``None`` on handled failure.
    """
    run_logger = get_run_logger()
    vid_id = video.id

    # Idempotent: a DONE video is never re-extracted; this guards manual reruns
    # from a redundant ffmpeg + vault write (the claim gate already excludes it).
    if video.audio_phase == "DONE":
        run_logger.info(f"Audio already stored for {vid_id} — skipping")
        return None

    if not video.raw_uri:
        run_logger.error(f"No raw_uri for {vid_id} though fetched=TRUE — marking failed")
        await VideoRepository().mark_failed(vid_id)
        return None

    v = get_vault()
    raw_buf = await asyncio.to_thread(v.fetch_binary, video.raw_uri)
    if raw_buf is None:
        run_logger.error(f"Raw artifact missing for {vid_id} at {video.raw_uri} — marking failed")
        await VideoRepository().mark_failed(vid_id)
        return None

    tmpdir = tempfile.mkdtemp(prefix="singer-audio-")
    try:
        raw_path = Path(tmpdir) / Path(video.raw_uri).name
        raw_path.write_bytes(raw_buf.getvalue())

        duration = getattr(video, "duration", None)
        chunks = _plan_audio_chunks(duration)
        results: list[tuple[str, str, bytes]] = []
        for start, length, idx in chunks:
            opus_path = Path(tmpdir) / f"{vid_id}_{idx:03d}.opus"
            try:
                if length is None:
                    await asyncio.to_thread(extract_audio_ffmpeg, raw_path, opus_path)
                else:
                    await asyncio.to_thread(
                        extract_audio_chunk, raw_path, opus_path, start, length
                    )
            except subprocess.TimeoutExpired:
                run_logger.warning(
                    f"Audio extraction timed out for {vid_id} (chunk {idx}) — releasing for retry"
                )
                await VideoRepository().release_to_pending(vid_id)
                return None
            except AudioExtractionError as e:
                run_logger.exception(f"Audio extraction failed for {vid_id}: {e}")
                await VideoRepository().mark_failed(vid_id)
                return None
            except Exception as e:
                run_logger.exception(f"Singer failed on {vid_id} (chunk {idx}): {e}")
                await VideoRepository().release_to_pending(vid_id)
                return None
            audio_bytes = opus_path.read_bytes()
            rel = (
                audio_path(vid_id)
                if length is None
                else f"audio/{vid_id}/{idx:03d}.opus"
            )
            results.append((vid_id, rel, audio_bytes))
        run_logger.info(f"Extracted {len(results)} audio chunk(s) for {vid_id}")
        return results
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _plan_audio_chunks(duration: int | None) -> list[tuple[float, float | None, int]]:
    """Return ``(start, length, idx)`` segments. Single full extract when the
    duration is unknown or within one chunk."""
    if not duration or duration <= SINGER_CHUNK_SECONDS:
        return [(0.0, None, 0)]
    n = math.ceil(duration / SINGER_CHUNK_SECONDS)
    return [
        (
            float(i * SINGER_CHUNK_SECONDS),
            float(min(SINGER_CHUNK_SECONDS, duration - i * SINGER_CHUNK_SECONDS)),
            i,
        )
        for i in range(n)
    ]


@flow(name="run_singer_cycle")
async def singer_flow(batch_size: int) -> dict[str, Any]:
    """Execute a complete Singer cycle: extract + store audio for fetched videos."""
    return await SingerAgent().run(batch_size=batch_size)


class SingerAgent(BaseBatchAgent):
    """Singer Agent: extract + store audio (no transcription)."""

    name = "singer"
    default_batch_size = 10
    max_concurrent = MAX_CONCURRENT_VIDEOS
    throttle_seconds = SINGER_THROTTLE_SECONDS

    async def claim_batch(self, n: int) -> list[Video]:
        return await fetch_singer_targets_task(n)

    async def process_one(self, video: Video) -> list[tuple[str, str, bytes]] | None:
        return await store_audio_task(video)

    async def store_results(self, results: list[Any]) -> None:
        # store_audio_task returns a per-video *list* of chunks; flatten them.
        extracted: list[tuple[str, str, bytes]] = [
            item for r in results if isinstance(r, list) for item in r
        ]
        if not extracted:
            return
        items: list[tuple[str, bytes]] = [
            (rel, data) for _vid, rel, data in extracted
        ]
        vids = [_vid for _vid, _rel, _data in extracted]
        await commit_artifacts(
            items=items,
            video_ids=vids,
            mark_safe=VideoRepository().mark_audio_safe,
            on_failure=VideoRepository().release_to_pending,
            label="audio files",
            store=vault_op_with_retry,
            vault=get_vault,
        )
        # The raw artifact is shared with the painter (frames). Reclaim it only
        # once BOTH consumers have derived their output, so the painter never
        # finds its input gone.
        repo = VideoRepository()
        for vid in dict.fromkeys(vids):
            await repo.reclaim_raw_if_complete(vid)


def main() -> None:
    try:
        agent = SingerAgent()
        asyncio.run(singer_flow(batch_size=agent.default_batch_size))
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
