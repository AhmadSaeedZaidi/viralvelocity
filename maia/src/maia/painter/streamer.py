"""
Production-grade YouTube video streamer with anti-bot bypass strategies.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

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
    YouTube video downloader with cascading bypass strategies.

    Priority cascade:
    1. Android client (bypasses web-based CAPTCHA/auth checks)
    2. Web Safari + PO Token (desktop emulation)
    3. Authenticated cookies (burner account fallback)
    """

    def __init__(self, cookies_path: Optional[str] = None):
        """
        Initialize streamer with optional authentication.

        Args:
            cookies_path: Path to Netscape cookies.txt file (optional burner account)
        """
        self.cookies_path = Path(cookies_path) if cookies_path else None

        if self.cookies_path and not self.cookies_path.exists():
            logger.warning(f"Cookies file not found: {self.cookies_path}. Proceeding without auth.")
            self.cookies_path = None

    def _get_base_options(self) -> Dict[str, Any]:
        """Base yt-dlp options shared across all strategies."""
        return {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "socket_timeout": 30,
        }

    def _get_android_options(self) -> Dict[str, Any]:
        """Primary strategy: Android client (most reliable for server-side scraping)."""
        opts = self._get_base_options()
        opts.update(
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"],
                        "player_skip": ["webpage", "configs"],
                    }
                }
            }
        )

        if self.cookies_path:
            opts["cookiefile"] = str(self.cookies_path)

        return opts

    def _get_web_safari_options(self) -> Dict[str, Any]:
        """Secondary strategy: Web Safari with PO Token."""
        opts = self._get_base_options()
        opts.update(
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web_safari"],
                        "player_skip": ["configs"],
                    }
                }
            }
        )

        if self.cookies_path:
            opts["cookiefile"] = str(self.cookies_path)

        return opts

    def _get_authenticated_options(self) -> Dict[str, Any]:
        """Tertiary strategy: Full authentication with cookies (requires burner account)."""
        if not self.cookies_path:
            raise ValueError("Authenticated strategy requires cookies_path")

        opts = self._get_base_options()
        opts.update(
            {
                "cookiefile": str(self.cookies_path),
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web", "android"],
                        "player_skip": [],
                    }
                },
            }
        )

        return opts

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type(yt_dlp.utils.DownloadError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def extract_info(self, video_url: str) -> Dict[str, Any]:
        """
        Extract video metadata and stream info with fallback cascade.

        Args:
            video_url: YouTube video URL or video ID

        Returns:
            Video info dictionary from yt-dlp

        Raises:
            yt_dlp.utils.DownloadError: If all bypass strategies fail
        """
        if not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_url}"

        strategies = [
            ("Android Client", self._get_android_options()),
            ("Web Safari + PO Token", self._get_web_safari_options()),
        ]

        if self.cookies_path:
            strategies.append(("Authenticated (Cookies)", self._get_authenticated_options()))

        last_error = None
        for strategy_name, options in strategies:
            try:
                logger.info(f"Attempting video extraction with strategy: {strategy_name}")
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    logger.info(f"✓ Success with {strategy_name}")
                    return cast(Dict[str, Any], info)

            except yt_dlp.utils.DownloadError as e:
                last_error = e
                error_msg = str(e)

                if "Sign in to confirm" in error_msg or "bot" in error_msg:
                    logger.warning(
                        f"✗ {strategy_name} blocked by anti-bot. Trying next strategy..."
                    )
                elif "429" in error_msg:
                    logger.error("Rate limit detected (429). Implementing backoff...")
                    raise  # Let tenacity handle retry
                else:
                    logger.warning(f"✗ {strategy_name} failed: {error_msg}")

                continue

        # All strategies exhausted
        logger.critical(f"All bypass strategies failed for {video_url}")
        raise last_error or yt_dlp.utils.DownloadError("Video extraction failed")

    def extract_heatmap_peaks(
        self, heatmap_data: List[Dict[str, Any]], top_n: int = 5
    ) -> List[float]:
        """Extract top N peaks from video heatmap data."""
        if not heatmap_data:
            return []

        valid_points = [p for p in heatmap_data if "value" in p and "start_time" in p]
        sorted_points = sorted(valid_points, key=lambda x: x.get("value", 0), reverse=True)

        top_points = sorted_points[:top_n]
        return [p.get("start_time", 0.0) for p in top_points]
