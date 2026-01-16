"""Maia Painter: Video keyframe extraction agent."""

import asyncio
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import yt_dlp
from prefect import flow, get_run_logger, task
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential,
)

from atlas.adapters.maia import MaiaDAO
from atlas.vault import vault

logger = logging.getLogger(__name__)


class VideoStreamer:
    def __init__(self, video_id: str):
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"
        self.logger = get_run_logger()

    def get_info(self) -> Dict[str, Any]:
        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result: Dict[str, Any] = ydl.extract_info(self.url, download=False)
            return result

    def extract_heatmap_peaks(
        self, heatmap_data: List[Dict[str, Any]], top_n: int = 5
    ) -> List[float]:
        if not heatmap_data:
            return []

        # heatmap_data is typically [{'start_time': 0.0, 'end_time': 1.0, 'value': 0.1}, ...]
        # We sort by 'value' descending and take top N
        sorted_points = sorted(heatmap_data, key=lambda x: x.get("value", 0), reverse=True)
        top_points = sorted_points[:top_n]

        return [p.get("start_time", 0.0) for p in top_points]


@task(name="fetch_painter_targets")
async def fetch_painter_targets(batch_size: int = 5) -> List[Dict[str, Any]]:
    # Batch size small because processing is heavy (CV2 + Network)
    dao = MaiaDAO()
    return await dao.fetch_painter_batch(batch_size)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _store_visuals_to_vault_with_retry(vid_id: str, frames: List[Tuple[int, bytes]]) -> None:
    """Store visual evidence to vault with retry logic for network failures."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: vault.store_visual_evidence(vid_id, frames))


@task(name="process_frames")
async def process_frames(video: Dict[str, Any]) -> None:
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]

    try:
        # 1. Get Video Info (Stream + Metadata)
        streamer = VideoStreamer(vid_id)
        # Run blocking call in thread
        info = await asyncio.to_thread(streamer.get_info)

        stream_url = info.get("url")
        chapters = info.get("chapters", [])
        heatmap = info.get("heatmap", [])

        if not stream_url:
            run_logger.error(f"No stream URL found for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            run_logger.error(f"Failed to open video stream for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0

        # 2. Determine Keyframe Timestamps
        target_timestamps = set()

        # Strategy A: Chapters (Structure)
        if chapters:
            run_logger.info(f"Adding {len(chapters)} chapter start points for {vid_id}")
            for chap in chapters:
                target_timestamps.add(chap["start_time"])

        # Strategy B: Heatmap (Viral Peaks)
        if heatmap:
            peaks = streamer.extract_heatmap_peaks(heatmap, top_n=5)
            run_logger.info(f"Adding {len(peaks)} viral peaks for {vid_id}")
            for p in peaks:
                target_timestamps.add(p)

        # Strategy C: Fallback (Scale with Length)
        if not target_timestamps:
            run_logger.info(f"No chapters/heatmap for {vid_id}. Using fallback scaling.")
            num_frames = 5
            if duration > 600:
                num_frames = 10  # > 10 mins
            if duration > 1800:
                num_frames = 20  # > 30 mins

            steps = np.linspace(0, duration - 1, num_frames)
            target_timestamps.update(steps.tolist())

        # Sort timestamps to process sequentially (efficient seeking)
        sorted_timestamps = sorted(list(target_timestamps))

        # 3. Extract All Frames into Memory (do NOT upload per-frame)
        frames_to_vault: List[Tuple[int, bytes]] = []

        for i, ts in enumerate(sorted_timestamps):
            if ts > duration:
                continue

            frame_idx = int(ts * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()

            if ret:
                # Encode to JPG in memory
                encode_ret, buffer = cv2.imencode(".jpg", frame)
                if encode_ret:
                    image_bytes = buffer.tobytes()
                    # Collect frame with its index
                    frames_to_vault.append((frame_idx, image_bytes))

        cap.release()

        if not frames_to_vault:
            run_logger.warning(f"No frames extracted for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

        # 4. Store ALL frames as a SINGLE Parquet file in the Vault
        run_logger.info(f"Uploading {len(frames_to_vault)} frames to Vault for {vid_id}")
        await _store_visuals_to_vault_with_retry(vid_id, frames_to_vault)

        # 5. Mark as safe in DB (data is in vault)
        await dao.mark_video_visuals_safe(vid_id)
        run_logger.info(f"Painted {len(frames_to_vault)} keyframes for {vid_id}")

    except SystemExit:
        raise
    except Exception as e:
        run_logger.error(f"Painter failed on {vid_id}: {e}")
        await dao.mark_video_failed(vid_id)


@flow(name="run_painter_cycle")
async def run_painter_cycle(batch_size: int = 5) -> None:
    """
    Execute a complete Painter cycle: fetch videos, extract frames, store to Vault.

    Args:
        batch_size: Number of videos to process (default: 5, heavy processing)

    Returns:
        None
    """
    logger = get_run_logger()
    logger.info("=== Starting Painter Cycle ===")

    targets = await fetch_painter_targets(batch_size)

    if not targets:
        logger.info("No videos need visual processing. Painter cycle complete (idle).")
        return

    logger.info(f"Processing {len(targets)} videos...")

    for video in targets:
        await process_frames(video)

    logger.info(f"=== Painter Cycle Complete === Processed {len(targets)} videos")


def main() -> None:
    """Entry point for running the Painter as a standalone service."""
    try:
        asyncio.run(run_painter_cycle())  # type: ignore[arg-type]
    except KeyboardInterrupt:
        logger.info("Painter stopped by user (SIGINT)")
    except Exception as e:
        logger.exception(f"Painter failed with error: {e}")
        raise


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()
