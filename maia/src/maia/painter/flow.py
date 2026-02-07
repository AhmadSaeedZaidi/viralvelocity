"""Maia Painter: Video keyframe extraction agent."""

import argparse
import asyncio
import logging
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np
import yt_dlp
from atlas.adapters.maia import MaiaDAO
from atlas.vault import vault
from prefect import flow, get_run_logger, task
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _store_visuals_to_vault_with_retry(vid_id: str, frames: List[Tuple[int, bytes]]) -> None:
    """Store visual evidence to vault with retry logic for network failures."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: vault.store_visual_evidence(vid_id, frames))


class VideoStreamer:
    """Helper class for extracting video information and processing streams."""

    def __init__(self, video_id: str):
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"
        self.logger = get_run_logger()

    def get_info(self) -> Dict[str, Any]:
        """Extract video information including stream URL and metadata."""
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
        """Extract top N peaks from video heatmap data."""
        if not heatmap_data:
            return []

        sorted_points = sorted(heatmap_data, key=lambda x: x.get("value", 0), reverse=True)
        top_points = sorted_points[:top_n]

        return [p.get("start_time", 0.0) for p in top_points]


@task(name="fetch_painter_targets")
async def fetch_painter_targets_task(batch_size: int) -> List[Dict[str, Any]]:
    """Fetch videos that need visual processing."""
    dao = MaiaDAO()
    return await dao.fetch_painter_batch(batch_size)


@task(name="process_frames")
async def process_frames_task(video: Dict[str, Any]) -> None:
    """Extract and store keyframes for a single video."""
    dao = MaiaDAO()
    run_logger = get_run_logger()
    vid_id = video["id"]

    try:
        streamer = VideoStreamer(vid_id)
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

        target_timestamps = set()

        if chapters:
            run_logger.info(f"Adding {len(chapters)} chapter start points for {vid_id}")
            for chap in chapters:
                target_timestamps.add(chap["start_time"])

        if heatmap:
            peaks = streamer.extract_heatmap_peaks(heatmap, top_n=5)
            run_logger.info(f"Adding {len(peaks)} viral peaks for {vid_id}")
            for p in peaks:
                target_timestamps.add(p)

        if not target_timestamps:
            run_logger.info(f"No chapters/heatmap for {vid_id}. Using fallback scaling.")
            num_frames = 5
            if duration > 600:
                num_frames = 10
            if duration > 1800:
                num_frames = 20

            steps = np.linspace(0, duration - 1, num_frames)
            target_timestamps.update(steps.tolist())

        sorted_timestamps = sorted(list(target_timestamps))

        frames_to_vault: List[Tuple[int, bytes]] = []

        for i, ts in enumerate(sorted_timestamps):
            if ts > duration:
                continue

            frame_idx = int(ts * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()

            if ret:
                encode_ret, buffer = cv2.imencode(".jpg", frame)
                if encode_ret:
                    image_bytes = buffer.tobytes()
                    frames_to_vault.append((frame_idx, image_bytes))

        cap.release()

        if not frames_to_vault:
            run_logger.warning(f"No frames extracted for {vid_id}")
            await dao.mark_video_failed(vid_id)
            return

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
    """
    Execute a complete Painter cycle: fetch videos, extract frames, store to Vault.

    Args:
        batch_size: Number of videos to process (default: 5, heavy processing)

    Returns:
        Dictionary with cycle statistics
    """
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
    """
    Painter Agent: Video keyframe extraction and visual evidence storage.

    Implements the Agent protocol for polymorphic command dispatch.
    """

    name = "painter"

    def __init__(self) -> None:
        """Initialize the Painter agent."""
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
        """
        Execute a complete Painter cycle.

        Args:
            batch_size: Number of videos to process
            **kwargs: Additional arguments (ignored)

        Returns:
            Dictionary with cycle statistics
        """
        return await painter_flow(batch_size=batch_size)


@flow(name="run_painter_cycle")
async def run_painter_cycle(batch_size: int = 5) -> None:
    """
    Legacy function wrapper for backward compatibility.

    Prefer using PainterAgent directly for new code.
    """
    agent = PainterAgent()
    await agent.run(batch_size=batch_size)


@task(name="fetch_painter_targets")
async def fetch_painter_targets(batch_size: int = 5) -> Any:
    """Legacy function wrapper for backward compatibility."""
    return await fetch_painter_targets_task(batch_size)


@task(name="process_frames")
async def process_frames(video: Dict[str, Any]) -> None:
    """Legacy function wrapper for backward compatibility."""
    await process_frames_task(video)


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
