"""Maia Painter: Video keyframe extraction agent."""

import argparse
import asyncio
import functools
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, TypeVar, Union, cast

import cv2
import numpy as np
import yt_dlp
from atlas.adapters.maia import MaiaDAO
from atlas.vault import vault
from prefect import flow, get_run_logger, task
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_in_executor(func: Callable[..., T]) -> Callable[..., Coroutine[Any, Any, T]]:
    """Decorator to run blocking functions in the default executor."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    return wrapper


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _store_visuals_to_vault_with_retry(vid_id: str, frames: List[Tuple[int, bytes]]) -> None:
    """Store visual evidence to vault with retry logic for network failures."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: vault.store_visual_evidence(vid_id, frames))


class VideoStreamer:
    """Helper class for extracting video information and processing streams."""

    def __init__(self, video_id: str):
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"

    def get_info(self) -> Dict[str, Any]:
        """Extract video information including stream URL and metadata."""
        # Request progressive mp4 or HTTP-compatible streams to avoid complex DASH handling in OpenCV
        ydl_opts = {
            "format": "best[ext=mp4]/best[protocol^=http]",
            "quiet": True,
            "no_warnings": True,
            "force_ipv4": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # yt-dlp extract_info returns Any (untyped), so we must cast it to satisfy mypy
            info = ydl.extract_info(self.url, download=False)
            return cast(Dict[str, Any], info) if info else {}

    def extract_heatmap_peaks(
        self, heatmap_data: List[Dict[str, Any]], top_n: int = 5
    ) -> List[float]:
        """Extract top N peaks from video heatmap data."""
        if not heatmap_data:
            return []

        # Filter valid points and sort by replay intensity (value)
        valid_points = [p for p in heatmap_data if "value" in p and "start_time" in p]
        sorted_points = sorted(valid_points, key=lambda x: x.get("value", 0), reverse=True)

        top_points = sorted_points[:top_n]
        return [p.get("start_time", 0.0) for p in top_points]


@task(name="fetch_painter_targets")
async def fetch_painter_targets_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch videos that need visual processing."""
    dao = MaiaDAO()
    return await dao.fetch_painter_batch(batch_size)


def _extract_frames_blocking(
    stream_url: str, target_timestamps: List[float], duration: float, run_logger: Any
) -> List[Tuple[int, bytes]]:
    """
    Blocking worker function for OpenCV operations.
    Executed in a thread pool to avoid blocking the asyncio event loop.
    """
    cap = cv2.VideoCapture(stream_url)
    frames_to_vault: List[Tuple[int, bytes]] = []

    try:
        if not cap.isOpened():
            run_logger.error("Failed to open video stream. URL might be expired or 403 Forbidden.")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0

        for ts in target_timestamps:
            if ts > duration:
                continue

            frame_idx = int(ts * fps)

            # Seeking on remote HTTP streams is network-intensive
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

            ret, frame = cap.read()
            if ret:
                encode_ret, buffer = cv2.imencode(".jpg", frame)
                if encode_ret:
                    image_bytes = buffer.tobytes()
                    frames_to_vault.append((frame_idx, image_bytes))
            else:
                run_logger.warning(f"Failed to read frame at timestamp {ts}s (frame {frame_idx})")

    except Exception as e:
        run_logger.error(f"Error during frame extraction: {e}")
    finally:
        cap.release()

    return frames_to_vault


@task(name="process_frames")
async def process_frames_task(video: Dict[str, Any]) -> None:
    """Extract and store keyframes for a single video."""
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]

    try:
        # 1. Fetch Metadata
        streamer = VideoStreamer(vid_id)
        info = await asyncio.to_thread(streamer.get_info)

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
            num_frames = 5
            if duration > 600:
                num_frames = 10
            if duration > 1800:
                num_frames = 20

            if duration > 0:
                steps = np.linspace(0, duration - 1, num_frames)
                target_timestamps.update(steps.tolist())

        sorted_timestamps = sorted(list(target_timestamps))
        run_logger.info(f"Targeting {len(sorted_timestamps)} frames at: {sorted_timestamps}")

        # 3. Extract Frames (Off-thread)
        frames_to_vault = await asyncio.to_thread(
            _extract_frames_blocking, stream_url, sorted_timestamps, duration, run_logger
        )

        if not frames_to_vault:
            run_logger.warning(f"No frames extracted for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        # 4. Store to Vault
        run_logger.info(f"Uploading {len(frames_to_vault)} frames to Vault for {vid_id}")
        await _store_visuals_to_vault_with_retry(vid_id, frames_to_vault)

        await dao.mark_video_visuals_safe(vid_id)
        run_logger.info(f"Painted {len(frames_to_vault)} keyframes for {vid_id}")

    except SystemExit:
        raise
    except Exception as e:
        run_logger.error(f"Painter failed on {vid_id}: {e}")
        await dao.mark_video_failed(vid_id)


@flow(name="run_painter_cycle")
async def painter_flow(batch_size: int) -> Dict[str, Any]:
    """Execute a complete Painter cycle."""
    run_logger = get_run_logger()
    run_logger.info("=== Starting Painter Cycle ===")

    targets = await fetch_painter_targets_task(batch_size)

    if not targets:
        run_logger.info("No videos need visual processing. Painter cycle complete (idle).")
        return {"videos_processed": 0}

    run_logger.info(f"Processing {len(targets)} videos...")

    for video in targets:
        await process_frames_task(video)

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
        return await painter_flow(batch_size=batch_size)


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
