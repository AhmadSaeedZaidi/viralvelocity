"""
Maia Painter: Stealth Video Streamer — Direct yt-dlp with Cookie Authentication.

Architecture:
- SOLE BACKEND: yt-dlp with authenticated cookies.txt (provided by atlas config)
- Cookie path is resolved exclusively through ``atlas.config.settings``.
- No Invidious proxies, no instance rotation, no conditional fallbacks.
"""

import logging
from pathlib import Path
from typing import Any, cast

import yt_dlp
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class StealthVideoStreamer:
    """
    YouTube video streamer using authenticated yt-dlp extraction.

    Cookie authentication is resolved from ``atlas.config.settings`` at
    construction time.  Direct ``os.environ`` access is forbidden.
    """

    def __init__(self, cookies_path: str | None = None) -> None:
        """Initialise streamer.

        Args:
            cookies_path: Explicit override for cookie file path.
                          When *None* (default) the path is resolved from
                          ``atlas.config.settings.youtube_cookies_resolved_path``.
        """
        if cookies_path is not None:
            resolved: str | None = cookies_path
        else:
            from atlas.config import settings

            resolved = settings.youtube_cookies_resolved_path

        self.cookies_path: Path | None = None
        if resolved:
            p = Path(resolved)
            if p.exists():
                self.cookies_path = p
            else:
                logger.warning(f"Cookies file not found: {p}. Proceeding without auth.")

    # ── yt-dlp option builders ───────────────────────────────────────────

    def _get_base_options(self) -> dict[str, Any]:
        """Base yt-dlp options shared across all strategies."""
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "socket_timeout": 5,
            # Force mp4 for OpenCV compatibility
            "format": "best[ext=mp4]/best",
            "force_ipv4": True,
            "extractor_retries": 3,
        }
        if self.cookies_path:
            opts["cookiefile"] = str(self.cookies_path)
        return opts

    def _get_primary_options(self) -> dict[str, Any]:
        """Primary strategy: web client with cookies."""
        opts = self._get_base_options()
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["web", "android"],
                "player_skip": [],
            }
        }
        return opts

    def _get_fallback_options(self) -> dict[str, Any]:
        """Fallback strategy: TV / mobile clients (less likely to be blocked)."""
        opts = self._get_base_options()
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["tv", "ios", "android"],
            }
        }
        return opts

    # ── Extraction ───────────────────────────────────────────────────────

    @staticmethod
    def _resolve_stream_url(info: dict[str, Any]) -> str | None:
        """Ensure we have a usable stream URL, preferring mp4 for OpenCV."""
        stream_url = info.get("url")
        if stream_url:
            return cast(str, stream_url)

        formats: list[dict[str, Any]] = info.get("formats", [])
        mp4_formats = [f for f in formats if f.get("ext") == "mp4" and f.get("url")]
        if mp4_formats:
            best = max(
                mp4_formats,
                key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
            )
            return cast(str, best["url"])

        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type(yt_dlp.utils.DownloadError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def extract_info(self, video_url: str) -> dict[str, Any]:
        """Extract video metadata and stream info via yt-dlp.

        Tries the primary (web + cookies) strategy first, then falls back
        to TV/mobile clients.

        Args:
            video_url: YouTube video URL or bare video ID.

        Returns:
            Video info dict with at minimum ``url``, ``duration``,
            ``chapters``, ``heatmap``.

        Raises:
            yt_dlp.utils.DownloadError: If all strategies fail.
        """
        if not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_url}"

        strategies = [
            ("Primary (Web + Cookies)", self._get_primary_options()),
            ("Fallback (TV/Mobile)", self._get_fallback_options()),
        ]

        last_error: Exception | None = None

        for name, options in strategies:
            try:
                logger.info(f"[yt-dlp] Attempting: {name}")
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    if info:
                        logger.info(f"[yt-dlp] Success with {name}")
                        return cast(dict[str, Any], info)
            except yt_dlp.utils.DownloadError as e:
                last_error = e
                error_msg = str(e)
                if "429" in error_msg:
                    logger.error("Rate limit (429). Propagating for backoff...")
                    raise
                logger.warning(f"[yt-dlp] {name} failed: {error_msg}")
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"[yt-dlp] {name} error: {e}")
                continue

        raise last_error or yt_dlp.utils.DownloadError("All extraction strategies exhausted")

    def extract_heatmap_peaks(
        self, heatmap_data: list[dict[str, Any]], top_n: int = 5
    ) -> list[float]:
        """Extract top *N* peak timestamps from video heatmap data.

        Args:
            heatmap_data: Heatmap points with ``start_time`` and ``value``.
            top_n: Number of peaks to return.

        Returns:
            Timestamps sorted by engagement value (descending).
        """
        if not heatmap_data:
            return []

        valid_points = [p for p in heatmap_data if "value" in p and "start_time" in p]
        sorted_points = sorted(valid_points, key=lambda x: x.get("value", 0), reverse=True)
        return [p.get("start_time", 0.0) for p in sorted_points[:top_n]]
