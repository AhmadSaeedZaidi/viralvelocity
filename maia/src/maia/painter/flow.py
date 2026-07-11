"""Maia Painter: Video keyframe extraction agent (Turbo Mode: FFmpeg + Concurrency).

Consumer in the Producer-Consumer pipeline. Pulls videos needing
visual processing from the video table, extracts keyframes via FFmpeg,
and persists them to Atlas Vault.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from atlas.models import Video
from atlas.repositories import VideoRepository
from atlas.state import clear_quota_exhausted
from atlas.utils import QuotaExhaustedError
from atlas.vault import get_vault, meta_path
from prefect import flow, get_run_logger, task

from maia.painter.streamer import StealthVideoStreamer, StreamRateLimitError
from maia.utils import notify_quota_exhausted, vault_op_with_retry

logger = logging.getLogger(__name__)

# CONCURRENCY CONTROL
# The VPS egress IP is flagged by YouTube, so keep concurrency low to avoid
# HTTP 429 (Too Many Requests). Each video also issues many FFmpeg range
# requests, compounding the per-IP load.
MAX_CONCURRENT_VIDEOS = 2

# ── Frame-extraction policy ───────────────────────────────────────────────────
# Frames are sampled on a uniform grid (one every FRAME_INTERVAL_SECONDS) and
# augmented with chapter starts and "most replayed" heatmap peaks, then clamped
# to [MIN_FRAMES, MAX_FRAMES]. Frames are downscaled to FRAME_HEIGHT (never
# upscaled) and JPEG-encoded at FRAME_JPEG_QUALITY. Frame grabs cost no API
# quota — only bandwidth/CPU/storage — so density can be tuned freely.
FRAME_INTERVAL_SECONDS = 15.0
MIN_FRAMES = 4
MAX_FRAMES = 60
HEATMAP_PEAKS = 8
FRAME_HEIGHT = 720
# Stored frame format. WebP is ~50% smaller than JPEG at comparable quality and
# is universally supported by ML/vision tooling.
FRAME_FORMAT = "webp"
FRAME_WEBP_QUALITY = 80  # libwebp -quality (0–100)
FRAME_JPEG_QUALITY = 3   # ffmpeg -q:v fallback when FRAME_FORMAT="jpg" (≈ q90)
# Prefer a source stream at or just above the target height to minimise download.
# Prefer an efficient codec (AV1/VP9) for the frame source, but keep it at full
# 720p — keyframes are analysed at native resolution, not downscaled.
STREAM_TARGET_HEIGHT = 720


def plan_timestamps(
    duration: float,
    chapter_starts: list[float] | None = None,
    heatmap_peaks: list[float] | None = None,
) -> list[float]:
    """Plan the set of timestamps (seconds) to sample for a video.

    Combines a uniform grid with chapter starts and heatmap peaks, de-duplicates,
    then clamps the count to ``[MIN_FRAMES, MAX_FRAMES]`` (evenly downsampling
    when over, back-filling uniformly when under).
    """
    if duration <= 0:
        return []

    last = max(0.0, duration - 1.0)
    timestamps: set[float] = set()

    # Uniform grid every FRAME_INTERVAL_SECONDS.
    n_grid = int(duration // FRAME_INTERVAL_SECONDS)
    for i in range(n_grid + 1):
        timestamps.add(min(i * FRAME_INTERVAL_SECONDS, last))

    # Salient points: chapters + heatmap peaks (rounded to reduce near-dupes).
    for pts in (chapter_starts or [], heatmap_peaks or []):
        for t in pts:
            if 0.0 <= t <= last:
                timestamps.add(round(float(t), 1))

    ordered = sorted(timestamps)

    # Back-fill to the minimum with an even spread.
    if len(ordered) < MIN_FRAMES:
        ordered = sorted(set(np.linspace(0.0, last, MIN_FRAMES).tolist()))

    # Downsample evenly to the maximum.
    if len(ordered) > MAX_FRAMES:
        idx = sorted(set(np.linspace(0, len(ordered) - 1, MAX_FRAMES).round().astype(int)))
        ordered = [ordered[i] for i in idx]

    return ordered


def select_stream_url(
    info: dict[str, Any], target_height: int = STREAM_TARGET_HEIGHT
) -> str | None:
    """Pick a video stream URL for frame extraction.

    Frames are keyframe stills, not full video, so we keep the source light:
    prefer **efficient codecs (AV1/VP9)** at **≤ *target_height***. Falls back to
    the tallest available video-only format, then to the default url.
    """
    video_formats = [
        f
        for f in info.get("formats", [])
        if f.get("url") and f.get("height") and f.get("vcodec", "none") != "none"
    ]
    if not video_formats:
        default_url: str | None = info.get("url")
        return default_url

    # Prefer low-res (<= target) to keep the frame source bandwidth-light; among
    # those, prefer efficient codecs, then the tallest (best quality) within cap.
    le = [f for f in video_formats if f["height"] <= target_height]
    pool = le or video_formats

    def _score(f: dict[str, Any]) -> tuple[int, int]:
        codecs = (f.get("vcodec") or "").lower()
        efficient = 0 if ("av1" in codecs or "vp9" in codecs) else 1
        return (efficient, -f["height"])

    return str(min(pool, key=_score)["url"])


@task(name="fetch_painter_targets")
async def fetch_painter_targets_task(batch_size: int) -> list[Video]:
    """Fetch videos that need visual processing."""
    video_repo = VideoRepository()
    return await video_repo.claim_painter_batch(batch_size)


def _is_valid_image(data: bytes, ext: str) -> bool:
    """Reject truncated/garbage frames before they reach the vault.

    WebP is a RIFF container (``RIFF<size>WEBP``); JPEG starts with the SOI
    marker ``FF D8 FF``. A frame that fails this check would otherwise be
    stored as a corrupt file in the vault.
    """
    if not data or len(data) < 32:
        return False
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return data[:3] == b"\xff\xd8\xff"


def _ffmpeg_extract_frame(stream_url: str, timestamp: float) -> bytes | None:
    """
    SURGICAL EXTRACTION: Uses FFmpeg to seek and grab a single frame.

    Much faster than OpenCV for remote streams because FFmpeg handles HTTP Range
    requests and network seeking optimally. Only downloads the bytes needed for
    a single frame instead of buffering the entire video.

    The frame is encoded to a *temp file*, never piped to stdout. The WebP muxer
    requires a seekable output, so piping WebP to ``-`` corrupts the RIFF
    container — that was producing invalid images in the vault. JPEG also pipes
    unreliably, so a temp file is used for both formats.

    Args:
        stream_url: Direct stream URL (mp4 preferred for seeking)
        timestamp: Target timestamp in seconds

    Returns:
        Encoded image bytes (webp/jpeg) or None if extraction failed
    """
    fd, out_path = tempfile.mkstemp(suffix=f".{FRAME_FORMAT}")
    os.close(fd)
    try:
        # Downscale to the target height (never upscale), preserving aspect.
        scale = f"scale=-2:'min(ih,{FRAME_HEIGHT})'"
        if FRAME_FORMAT == "webp":
            codec_args = ["-c:v", "libwebp", "-quality", str(FRAME_WEBP_QUALITY), "-f", "webp"]
        else:
            codec_args = ["-c:v", "mjpeg", "-q:v", str(FRAME_JPEG_QUALITY), "-f", "image2"]

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-ss", str(timestamp),
            "-i", stream_url,
            "-frames:v", "1",
            "-vf", scale,
            *codec_args,
            out_path,
        ]

        process = subprocess.run(cmd, capture_output=True, timeout=20)

        if process.returncode != 0:
            if process.stderr:
                logger.debug(
                    f"FFmpeg stderr at {timestamp}s: {process.stderr.decode(errors='replace')}"
                )
            return None

        with Path(out_path).open("rb") as fh:
            data = fh.read()

        if not _is_valid_image(data, FRAME_FORMAT):
            logger.warning(
                f"FFmpeg produced an invalid {FRAME_FORMAT} frame at {timestamp}s"
            )
            return None

        return data

    except subprocess.TimeoutExpired:
        logger.warning(f"FFmpeg timeout at {timestamp}s")
        return None
    except Exception as e:
        logger.warning(f"FFmpeg failed at {timestamp}s: {e}")
        return None
    finally:
        with contextlib.suppress(OSError):
            Path(out_path).unlink()


def _extract_frames_surgical(
    stream_url: str, target_timestamps: list[float], duration: float, run_logger: Any
) -> list[tuple[int, bytes]]:
    """
    Worker function using FFmpeg for surgical frame extraction.

    Executed in a thread pool to avoid blocking the asyncio event loop.
    Each frame is extracted independently via HTTP Range requests.
    """
    frames_to_vault: list[tuple[int, bytes]] = []

    fps = 30.0

    for ts in target_timestamps:
        if ts > duration:
            continue

        frame_idx = int(ts * fps)

        image_bytes = _ffmpeg_extract_frame(stream_url, ts)

        if image_bytes:
            frames_to_vault.append((frame_idx, image_bytes))
        else:
            run_logger.warning(f"FFmpeg failed to grab frame at {ts}s")

    return frames_to_vault


@task(name="process_frames")
async def process_frames_task(video: Video) -> tuple[str, list[tuple[int, bytes]]] | None:
    """Extract keyframes for a single video (FFmpeg surgical extraction).

    **Preferred path (no extra YouTube session):** read the ``info.json`` the
    unified streamer stashed in the vault (``meta/{id}.info.json``), pick a
    low-res AV1/VP9 stream URL, and pull frames via **HTTP range requests**
    straight from YouTube's CDN. This keeps the whole pipeline to a single
    YouTube session (the streamer's) while staying bandwidth-light.

    **Fallback path:** legacy rows with no vault metadata get a fresh YouTube
    ``extract_info`` (the only other YouTube touch) to obtain a current stream
    URL; the stashed CDN URL is also refreshed once if it has expired.

    Returns ``(video_id, frames_to_vault)`` on success, or ``None`` on hard
    failure (video marked FAILED). Storage is deferred to the flow level.
    """
    video_repo = VideoRepository()
    run_logger = get_run_logger()
    vid_id = video.id

    tmpdir = tempfile.mkdtemp(prefix="painter-raw-")
    try:
        v = get_vault()
        local_input: str | None = None
        duration = 0.0
        chapters: list[float] = []
        heatmap: list[float] = []

        # Preferred path: the unified streamer stashed the video's *stream URLs*
        # (and metadata) in the vault as meta/{id}.info.json. We pull frames via
        # HTTP range requests straight from YouTube's CDN — no full download and
        # no second YouTube metadata session. (The raw artifact is audio-only and
        # is consumed by the singer, not the painter.)
        meta_buf = await asyncio.to_thread(v.fetch_binary, meta_path(vid_id))
        info: dict[str, Any] | None = None
        if meta_buf is not None:
            try:
                info = json.loads(meta_buf.getvalue())
            except Exception:
                info = None

        if info is not None:
            stream_url = select_stream_url(info)
            if stream_url:
                local_input = stream_url
                duration = info.get("duration", 0) or 0
                chapters = [c.get("start_time", 0.0) for c in info.get("chapters", []) or []]
                heatmap = StealthVideoStreamer().extract_heatmap_peaks(
                    info.get("heatmap", []) or [], top_n=HEATMAP_PEAKS
                )

        # Fallback (legacy rows missing vault metadata): fresh YouTube metadata.
        if local_input is None:
            run_logger.info(f"No vault metadata for {vid_id}; falling back to YouTube frames")
            streamer = StealthVideoStreamer()
            info = await asyncio.to_thread(streamer.extract_info, vid_id)
            duration = info.get("duration", 0) or 0
            stream_url = select_stream_url(info)
            if not stream_url:
                run_logger.error(f"No stream URL found for {vid_id}")
                await video_repo.mark_failed(vid_id)
                return None
            chapters = [c.get("start_time", 0.0) for c in info.get("chapters", []) or []]
            heatmap = streamer.extract_heatmap_peaks(
                info.get("heatmap", []) or [], top_n=HEATMAP_PEAKS
            )
            local_input = stream_url

        sorted_timestamps = plan_timestamps(duration, chapters, heatmap)
        run_logger.info(
            f"Targeting {len(sorted_timestamps)} frames for {vid_id} "
            f"(duration={duration}s, chapters={len(chapters)}, peaks={len(heatmap)})"
        )

        frames_to_vault = await asyncio.to_thread(
            _extract_frames_surgical, local_input, sorted_timestamps, duration, run_logger
        )

        if not frames_to_vault:
            # The stashed CDN URL is signed and short-TTL; it may have expired.
            # Retry once with a fresh YouTube metadata pull before giving up.
            if meta_buf is not None:
                run_logger.warning(
                    f"Frame pull failed for {vid_id} via vault URL (likely expired); refreshing"
                )
                streamer = StealthVideoStreamer()
                info = await asyncio.to_thread(streamer.extract_info, vid_id)
                stream_url = select_stream_url(info)
                if stream_url:
                    frames_to_vault = await asyncio.to_thread(
                        _extract_frames_surgical,
                        stream_url,
                        sorted_timestamps,
                        duration,
                        run_logger,
                    )
            if not frames_to_vault:
                run_logger.warning(f"No frames extracted for {vid_id}")
                await video_repo.mark_failed(vid_id)
                return None

        run_logger.info(f"Extracted {len(frames_to_vault)} keyframes for {vid_id}")
        return (vid_id, frames_to_vault)

    except QuotaExhaustedError:
        await notify_quota_exhausted("painter")
        raise
    except StreamRateLimitError as e:
        # Transient — release back to PENDING so it retries on a later cycle
        # instead of being permanently marked FAILED.
        run_logger.warning(f"Rate-limited on {vid_id}, releasing for retry: {e}")
        await video_repo.release_to_pending(vid_id)
        return None
    except Exception as e:
        run_logger.exception(f"Painter failed on {vid_id}: {e}")
        await video_repo.mark_failed(vid_id)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@flow(name="run_painter_cycle")
async def painter_flow(batch_size: int) -> dict[str, Any]:
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

    sem = asyncio.Semaphore(MAX_CONCURRENT_VIDEOS)

    async def protected_process(video: Video) -> tuple[str, list[tuple[int, bytes]]] | None:
        async with sem:
            await asyncio.sleep(random.uniform(0.5, 2.0))
            return await process_frames_task(video)

    results = await asyncio.gather(
        *[protected_process(v) for v in targets], return_exceptions=True
    )
    # Propagate any QuotaExhaustedError that was caught by return_exceptions
    quota_errors = [r for r in results if isinstance(r, QuotaExhaustedError)]
    if quota_errors:
        raise quota_errors[0]

    # Collect successfully-extracted frames and write the WHOLE batch to the
    # vault in a SINGLE commit (instead of 1 commit per video). This keeps us
    # far under HuggingFace's 128-commits/hour account cap during bulk
    # recollection. Any video whose extraction failed has already been marked
    # FAILED/RELEASED inside process_frames_task and is absent from `extracted`.
    extracted: list[tuple[str, list[tuple[int, bytes]]]] = [
        r for r in results if isinstance(r, tuple)
    ]

    if extracted:
        video_repo = VideoRepository()
        v = get_vault()
        entries = [(vid, frames, FRAME_FORMAT) for vid, frames in extracted]
        try:
            await vault_op_with_retry(lambda: v.store_visual_evidence_batch(entries))
            for vid, _ in extracted:
                await video_repo.mark_visuals_safe(vid)
            run_logger.info(
                f"Batched {len(extracted)} videos' frames into ONE vault commit"
            )
        except Exception as e:
            run_logger.exception(f"Batched frame store failed ({len(extracted)} vids): {e}")
            for vid, _ in extracted:
                await video_repo.mark_failed(vid)

    # Cycle completed without quota exhaustion — clear any stale marker so the
    # heartbeat stops reporting painter as rate-limited.
    clear_quota_exhausted("painter")

    run_logger.info(f"=== Painter Cycle Complete === Processed {len(targets)} videos")
    return {"videos_processed": len(targets)}


class PainterAgent:
    """Painter Agent: Video keyframe extraction."""

    name = "painter"

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
        result: dict[str, Any] = await painter_flow(batch_size=batch_size)
        return result


@task(name="fetch_painter_targets")
async def fetch_painter_targets(batch_size: int = 5) -> Any:
    return await fetch_painter_targets_task(batch_size)


@flow(name="run_painter_cycle")
async def run_painter_cycle(batch_size: int = 5) -> None:
    agent = PainterAgent()
    await agent.run(batch_size=batch_size)


def main() -> None:
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
