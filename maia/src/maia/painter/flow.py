"""Maia Painter: Video keyframe extraction agent."""

import argparse
import asyncio
import logging
import functools
from typing import Any, Dict, List, Tuple, Optional, Set

import cv2
import numpy as np
import yt_dlp
from atlas.adapters.maia import MaiaDAO
from atlas.vault import vault
from prefect import flow, get_run_logger, task
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

# Configure module-level logger
logger = logging.getLogger(__name__)


def run_in_executor(func):
    """Decorator to run blocking functions in the default executor."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
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
        # 'best' can return DASH (video-only) streams which OpenCV cannot handle easily.
        # We explicitly request a progressive mp4 or a stream served via http/https protocol
        # that includes both audio/video or is compatible with simple players.
        ydl_opts = {
            "format": "best[ext=mp4]/best[protocol^=http]",
            "quiet": True,
            "no_warnings": True,
            "force_ipv4": True,  # Improves stability in some containerized envs
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # download=False ensures we just get the metadata/URL
            result: Dict[str, Any] = ydl.extract_info(self.url, download=False)
            return result

    def extract_heatmap_peaks(
        self, heatmap_data: List[Dict[str, Any]], top_n: int = 5
    ) -> List[float]:
        """
        Extract top N peaks from video heatmap data.
        
        Heatmap data from yt-dlp is typically a list of segments with 'value' (0-1).
        We want the start_time of the segments with the highest replay intensity.
        """
        if not heatmap_data:
            return []

        # Filter out invalid points and sort by 'value' (replay frequency) descending
        valid_points = [p for p in heatmap_data if "value" in p and "start_time" in p]
        sorted_points = sorted(valid_points, key=lambda x: x.get("value", 0), reverse=True)
        
        # Take the top N viral moments
        top_points = sorted_points[:top_n]

        return [p.get("start_time", 0.0) for p in top_points]


@task(name="fetch_painter_targets")
async def fetch_painter_targets_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch videos that need visual processing."""
    dao = MaiaDAO()
    return await dao.fetch_painter_batch(batch_size)


def _extract_frames_blocking(
    stream_url: str,
    target_timestamps: List[float],
    duration: float,
    run_logger: logging.Logger
) -> List[Tuple[int, bytes]]:
    """
    Blocking worker function for OpenCV operations.
    Executed in a thread pool to avoid blocking the asyncio event loop.
    """
    # OpenCV's VideoCapture is blocking and not async-friendly.
    cap = cv2.VideoCapture(stream_url)
    frames_to_vault: List[Tuple[int, bytes]] = []

    try:
        if not cap.isOpened():
            run_logger.error("Failed to open video stream. URL might be expired or 403 Forbidden.")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        # Fallback if FPS is missing/zero (common in some streams)
        if fps <= 0:
            fps = 30.0

        for ts in target_timestamps:
            if ts > duration:
                continue

            frame_idx = int(ts * fps)
            
            # Seeking on remote HTTP streams is slow (O(N) network operation).
            # This logic is correct for requirements but performant only on fast connections.
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            
            ret, frame = cap.read()
            if ret:
                # Encode directly to memory buffer (.jpg)
                encode_ret, buffer = cv2.imencode(".jpg", frame)
                if encode_ret:
                    image_bytes = buffer.tobytes()
                    frames_to_vault.append((frame_idx, image_bytes))
            else:
                run_logger.warning(f"Failed to read frame at timestamp {ts}s (frame {frame_idx})")

    except Exception as e:
        run_logger.error(f"Error during frame extraction: {e}")
    finally:
        # Crucial: Always release the capture to free sockets/file descriptors
        cap.release()

    return frames_to_vault


@task(name="process_frames")
async def process_frames_task(video: Dict[str, Any]) -> None:
    """
    Extract and store keyframes for a single video.
    
    Logic:
    1. Fetch metadata (chapters, heatmap, stream URL).
    2. Identify 'Target Timestamps' based on chapters and viral peaks.
    3. Extract frames at those timestamps.
    4. Store to Vault.
    """
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]

    try:
        # 1. Get Info (IO Bound - runs in thread to be safe)
        streamer = VideoStreamer(vid_id)
        info = await asyncio.to_thread(streamer.get_info)

        stream_url = info.get("url")
        chapters = info.get("chapters", [])
        heatmap = info.get("heatmap", [])  # 'heatmap' key from yt-dlp
        duration = info.get("duration", 0)

        if not stream_url:
            run_logger.error(f"No stream URL found for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        # 2. Identify Target Timestamps
        target_timestamps: Set[float] = set()

        # Requirement A: Check Chapters
        if chapters:
            run_logger.info(f"Adding {len(chapters)} chapter start points for {vid_id}")
            for chap in chapters:
                target_timestamps.add(chap.get("start_time", 0.0))

        # Requirement B: Check Histogram (Heatmap)
        if heatmap:
            peaks = streamer.extract_heatmap_peaks(heatmap, top_n=5)
            run_logger.info(f"Adding {len(peaks)} viral peaks for {vid_id}")
            for p in peaks:
                target_timestamps.add(p)

        # Fallback: If no smart metadata, use linear spacing
        if not target_timestamps:
            run_logger.info(f"No chapters/heatmap for {vid_id}. Using fallback linear scaling.")
            num_frames = 5
            if duration > 600: num_frames = 10
            if duration > 1800: num_frames = 20

            if duration > 0:
                steps = np.linspace(0, duration - 1, num_frames)
                target_timestamps.update(steps.tolist())

        sorted_timestamps = sorted(list(target_timestamps))
        run_logger.info(f"Targeting {len(sorted_timestamps)} frames at: {sorted_timestamps}")

        # 3. Extract Frames (CPU/IO Blocking - OFF THE MAIN THREAD)
        frames_to_vault = await asyncio.to_thread(
            _extract_frames_blocking, 
            stream_url, 
            sorted_timestamps, 
            duration, 
            run_logger
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

    async def run(self, batch_size: int = 5, **kwargs: Any) -> Dict[str, Any]:
        return await painter_flow(batch_size=batch_size)


@task(name="fetch_painter_targets")
async def fetch_painter_targets(batch_size: int = 5) -> Any:
    return await fetch_painter_targets_task(batch_size)


@task(name="process_frames")
async def process_frames(video: Dict[str, Any]) -> None:
    await process_frames_task(video)