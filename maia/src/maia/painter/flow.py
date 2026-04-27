"""Maia Painter: Video keyframe extraction agent (Turbo Mode: FFmpeg + Concurrency)."""

import argparse
import asyncio
import logging
import random
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from atlas.adapters.maia import MaiaDAO
from atlas.vault import get_vault
from prefect import flow, get_run_logger, task

from maia.painter.streamer import StealthVideoStreamer
from maia.utils import RateLimitError, vault_op_with_retry

logger = logging.getLogger(__name__)

# CONCURRENCY CONTROL
# 5-8 is optimal to saturate network without triggering 429s
MAX_CONCURRENT_VIDEOS = 5


@task(name="fetch_painter_targets")
async def fetch_painter_targets_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch videos that need visual processing."""
    dao = MaiaDAO()
    return await dao.fetch_painter_batch(batch_size)


def _ffmpeg_extract_frame(stream_url: str, timestamp: float) -> Optional[bytes]:
    """
    SURGICAL EXTRACTION: Uses FFmpeg to seek and grab a single frame.

    Much faster than OpenCV for remote streams because FFmpeg handles HTTP Range
    requests and network seeking optimally. Only downloads the bytes needed for
    a single frame instead of buffering the entire video.

    Args:
        stream_url: Direct stream URL (mp4 preferred for seeking)
        timestamp: Target timestamp in seconds

    Returns:
        JPEG-encoded image bytes or None if extraction failed
    """
    try:
        # FFmpeg command breakdown:
        # -ss: Seek to timestamp (BEFORE -i for fast seek via HTTP Range)
        # -i: Input URL
        # -frames:v 1: Grab exactly 1 video frame
        # -f image2: Force image output format
        # -c:v mjpeg: Encode as JPEG
        # -: Output to stdout (pipe)
        # -y: Overwrite without confirmation
        # -hide_banner -loglevel error: Suppress noise
        cmd = [
            "ffmpeg",
            "-ss",
            str(timestamp),
            "-i",
            stream_url,
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-c:v",
            "mjpeg",
            "-",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]

        # Run with timeout to prevent hanging on dead streams.
        # 20s budget covers slow HTTP-Range seeks to mid/end of long videos
        # served by YouTube CDN edges.
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate(timeout=20)

        if process.returncode == 0 and out:
            return out
        else:
            if err:
                logger.debug(f"FFmpeg stderr at {timestamp}s: {err.decode()}")
            return None

    except subprocess.TimeoutExpired:
        process.kill()
        logger.warning(f"FFmpeg timeout at {timestamp}s")
        return None
    except Exception as e:
        logger.warning(f"FFmpeg failed at {timestamp}s: {e}")
        return None


def _extract_frames_surgical(
    stream_url: str, target_timestamps: List[float], duration: float, run_logger: Any
) -> List[Tuple[int, bytes]]:
    """
    Worker function using FFmpeg for surgical frame extraction.

    Executed in a thread pool to avoid blocking the asyncio event loop.
    Each frame is extracted independently via HTTP Range requests.
    """
    frames_to_vault: List[Tuple[int, bytes]] = []

    # Estimate FPS (default to 30 for frame index calculation)
    fps = 30.0

    for ts in target_timestamps:
        if ts > duration:
            continue

        frame_idx = int(ts * fps)

        # Use FFmpeg to grab the frame
        image_bytes = _ffmpeg_extract_frame(stream_url, ts)

        if image_bytes:
            frames_to_vault.append((frame_idx, image_bytes))
        else:
            run_logger.warning(f"FFmpeg failed to grab frame at {ts}s")

    return frames_to_vault


@task(name="process_frames")
async def process_frames_task(video: Dict[str, Any]) -> None:
    """Extract and store keyframes for a single video using FFmpeg surgical extraction."""
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]

    try:
        # 1. Fetch Metadata using StealthVideoStreamer (direct yt-dlp with cookies)
        streamer = StealthVideoStreamer()
        info = await asyncio.to_thread(streamer.extract_info, vid_id)

        stream_url = info.get("url")
        chapters = info.get("chapters", [])
        heatmap = info.get("heatmap", [])
        duration = info.get("duration", 0)

        if not stream_url:
            run_logger.error(f"No stream URL found for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        # 2. Identify Target Timestamps
        target_timestamps: Set[float] = set()

        if chapters:
            run_logger.info(f"Adding {len(chapters)} chapter start points for {vid_id}")
            for chap in chapters:
                target_timestamps.add(chap.get("start_time", 0.0))

        if heatmap:
            peaks = streamer.extract_heatmap_peaks(heatmap, top_n=5)
            run_logger.info(f"Adding {len(peaks)} viral peaks for {vid_id}")
            for p in peaks:
                target_timestamps.add(p)

        # Fallback: Linear spacing if no rich metadata exists
        if not target_timestamps:
            run_logger.info(f"No chapters/heatmap for {vid_id}. Using fallback linear scaling.")
            num_frames = 5 if duration < 600 else 10
            if duration > 1800:
                num_frames = 20

            if duration > 0:
                steps = np.linspace(0, duration - 1, num_frames)
                target_timestamps.update(steps.tolist())

        sorted_timestamps = sorted(list(target_timestamps))
        run_logger.info(f"Targeting {len(sorted_timestamps)} frames for {vid_id}")

        # 3. SURGICAL EXTRACTION: FFmpeg (Off-thread)
        frames_to_vault = await asyncio.to_thread(
            _extract_frames_surgical, stream_url, sorted_timestamps, duration, run_logger
        )

        if not frames_to_vault:
            run_logger.warning(f"No frames extracted for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        # 4. Store to Vault
        v = get_vault()
        await vault_op_with_retry(lambda: v.store_visual_evidence(vid_id, frames_to_vault))

        await dao.mark_video_visuals_safe(vid_id)
        run_logger.info(f"✅ Painted {len(frames_to_vault)} keyframes for {vid_id}")

    except RateLimitError:
        raise
    except Exception as e:
        run_logger.error(f"Painter failed on {vid_id}: {e}")
        await dao.mark_video_failed(vid_id)


@flow(name="run_painter_cycle")
async def painter_flow(batch_size: int) -> Dict[str, Any]:
    """
    Execute a complete Painter cycle with PARALLEL processing.

    Uses a semaphore to control concurrency (default: 5 concurrent videos).
    Each video is processed independently with FFmpeg surgical extraction.
    """
    run_logger = get_run_logger()
    run_logger.info(
        f"=== Starting Painter Cycle (Turbo Mode: {MAX_CONCURRENT_VIDEOS} concurrent) ==="
    )

    targets = await fetch_painter_targets_task(batch_size)

    if not targets:
        run_logger.info("No videos need visual processing. Painter cycle complete (idle).")
        return {"videos_processed": 0}

    run_logger.info(f"Processing {len(targets)} videos in parallel...")

    # CONCURRENCY CONTROL: Semaphore to limit simultaneous FFmpeg processes
    sem = asyncio.Semaphore(MAX_CONCURRENT_VIDEOS)

    async def protected_process(vid: Dict[str, Any]) -> None:
        """Process a single video with semaphore protection and jitter."""
        async with sem:
            # Add random jitter to stagger requests and avoid thundering herd
            await asyncio.sleep(random.uniform(0.5, 2.0))
            await process_frames_task(vid)

    # THE SWARM: Launch all tasks concurrently
    await asyncio.gather(*[protected_process(v) for v in targets], return_exceptions=True)

    run_logger.info(f"=== Painter Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class PainterAgent:
    """Painter Agent: Video keyframe extraction."""

    name = "painter"

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.name)

    @staticmethod
    def add_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments for the Painter agent."""
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5,
            help="Number of videos to process per cycle (default: 5)",
        )

    async def run(self, batch_size: int = 5, **kwargs: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = await painter_flow(batch_size=batch_size)
        return result


@task(name="fetch_painter_targets")
async def fetch_painter_targets(batch_size: int = 5) -> Any:
    return await fetch_painter_targets_task(batch_size)


@task(name="process_frames")
async def process_frames(video: Dict[str, Any]) -> None:
    await process_frames_task(video)


@flow(name="run_painter_cycle")
async def run_painter_cycle(batch_size: int = 5) -> None:
    """Legacy wrapper for backward compatibility."""
    agent = PainterAgent()
    await agent.run(batch_size=batch_size)


def main() -> None:
    """Entry point for running the Painter as a standalone service."""
    try:
        agent = PainterAgent()
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Painter stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Painter failed with error: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
