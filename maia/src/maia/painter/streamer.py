"""
Maia Painter: Stealth Video Streamer with Invidious Protocol.

Architecture (Fallback Cascade):
1. PRIMARY: Invidious federation (anonymous, accountless, IP-safe)
2. SECONDARY: yt-dlp Android client (direct, no cookies)
3. TERTIARY: yt-dlp Web Safari + PO Token
4. NUCLEAR: yt-dlp with cookies.txt (burner account)

The Invidious layer routes through federated instances, so YouTube
never sees our server IP. If the entire Invidious network is down,
we fall back to legacy direct extraction strategies.
"""

import asyncio
import concurrent.futures
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, cast

import aiohttp
import yt_dlp
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ─── Invidious Instance Management ────────────────────────────────────────────

INVIDIOUS_API_ENDPOINT = "https://api.invidious.io/instances.json?sort_by=health"

# Deadman Switch: If the directory API is down, use these battle-tested instances.
FALLBACK_INSTANCES: List[str] = [
    "https://inv.tux.pizza",
    "https://vid.puffyan.us",
    "https://yewtu.be",
    "https://yt.artemislena.eu",
]

# Timeout for Invidious instance discovery (seconds)
INSTANCE_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=10)


class InstanceManager:
    """
    Manages discovery, health-filtering, selection, and blacklisting
    of Invidious instances.

    Implements:
    - Dynamic Instance Discovery (api.invidious.io)
    - Strict Filtering (https, api, cors, no onion)
    - Session Blacklist (rotate on failure)
    - Deadman Switch (hardcoded fallback)
    - Hourly refresh cycle
    """

    def __init__(self) -> None:
        self._instances: List[str] = []
        self._blacklist: Set[str] = set()
        self._last_refresh: float = 0.0
        self._refresh_interval: float = 3600.0  # 1 hour

    async def _fetch_instances_from_api(self) -> List[str]:
        """Fetch and filter instances from the Invidious directory API."""
        try:
            async with aiohttp.ClientSession(timeout=INSTANCE_FETCH_TIMEOUT) as session:
                async with session.get(INVIDIOUS_API_ENDPOINT) as resp:
                    if resp.status != 200:
                        logger.error(
                            f"Invidious directory returned HTTP {resp.status}. "
                            f"Engaging Deadman Switch."
                        )
                        return []

                    data: Any = await resp.json()

                    valid: List[str] = []
                    for entry in data:
                        # Each entry is [domain_str, info_dict]
                        if not isinstance(entry, list) or len(entry) < 2:
                            continue

                        domain, info = entry[0], entry[1]

                        # Strict filtering per strategy doc
                        if not isinstance(info, dict):
                            continue
                        if info.get("type") != "https":
                            continue
                        if info.get("api") is not True:
                            continue
                        if info.get("cors") is not True:
                            continue
                        if "onion" in str(domain):
                            continue

                        uri = info.get("uri")
                        if uri and isinstance(uri, str):
                            valid.append(uri.rstrip("/"))

                    logger.info(f"Discovered {len(valid)} healthy Invidious instances.")
                    return valid

        except asyncio.TimeoutError:
            logger.error("Invidious directory API timed out. Engaging Deadman Switch.")
            return []
        except Exception as e:
            logger.error(f"Failed to fetch Invidious instances: {e}. Engaging Deadman Switch.")
            return []

    async def get_instance(self) -> str:
        """
        Get a random healthy instance, refreshing the pool if stale.

        Returns:
            Base URL of a healthy Invidious instance (e.g. "https://inv.tux.pizza")
        """
        now = time.time()
        needs_refresh = not self._instances or (now - self._last_refresh > self._refresh_interval)

        if needs_refresh:
            logger.info("Refreshing Invidious instance pool...")
            fresh = await self._fetch_instances_from_api()

            if fresh:
                self._instances = fresh
                self._last_refresh = now
                self._blacklist.clear()
            elif not self._instances:
                # Deadman Switch: API is down AND we have no cached instances
                logger.warning("Deadman Switch activated: using hardcoded fallback instances.")
                self._instances = list(FALLBACK_INSTANCES)
                self._last_refresh = now

        # Filter blacklisted instances
        candidates = [i for i in self._instances if i not in self._blacklist]

        if not candidates:
            logger.warning("All instances blacklisted! Resetting blacklist.")
            self._blacklist.clear()
            candidates = list(self._instances)

        return random.choice(candidates)

    def mark_bad(self, instance: str) -> None:
        """Blacklist an instance for this session."""
        logger.warning(f"Blacklisting Invidious instance: {instance}")
        self._blacklist.add(instance)

    @property
    def pool_size(self) -> int:
        """Number of instances currently in the pool (excluding blacklisted)."""
        return len([i for i in self._instances if i not in self._blacklist])

    def reset(self) -> None:
        """Force a full reset (useful for testing)."""
        self._instances.clear()
        self._blacklist.clear()
        self._last_refresh = 0.0


# Module-level singleton
_instance_manager = InstanceManager()


# ─── Stealth Video Streamer ──────────────────────────────────────────────────


class StealthVideoStreamer:
    """
    YouTube video streamer with cascading bypass strategies.

    Priority cascade:
    1. Invidious federation (anonymous, zero auth)
    2. yt-dlp Android client (direct, no cookies)
    3. yt-dlp Web Safari + PO Token
    4. yt-dlp with cookies.txt (nuclear option)

    OpenCV Compatibility:
    - Forces mp4 container format for all strategies
    - Avoids webm/DASH segments that OpenCV can't decode
    """

    # Maximum Invidious rotation attempts before falling back to direct yt-dlp
    MAX_INVIDIOUS_ATTEMPTS = 5

    def __init__(self, cookies_path: Optional[str] = None) -> None:
        """
        Initialize streamer.

        Args:
            cookies_path: Path to Netscape cookies.txt (nuclear option, optional)
        """
        self.cookies_path = Path(cookies_path) if cookies_path else None

        if self.cookies_path and not self.cookies_path.exists():
            logger.warning(f"Cookies file not found: {self.cookies_path}. Proceeding without auth.")
            self.cookies_path = None

    # ── yt-dlp option builders ───────────────────────────────────────────

    def _get_base_options(self) -> Dict[str, Any]:
        """Base yt-dlp options shared across all strategies."""
        return {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "socket_timeout": 5,
            # Format Trap: Force mp4 for OpenCV compatibility
            "format": "best[ext=mp4]/best",
            "force_ipv4": True,
        }

    def _get_android_options(self) -> Dict[str, Any]:
        """Secondary strategy: Android client (bypasses web CAPTCHA)."""
        opts = self._get_base_options()
        opts.update(
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"],
                        "player_skip": ["webpage", "configs"],
                    }
                },
            }
        )
        if self.cookies_path:
            opts["cookiefile"] = str(self.cookies_path)
        return opts

    def _get_web_safari_options(self) -> Dict[str, Any]:
        """Tertiary strategy: Web Safari + PO Token."""
        opts = self._get_base_options()
        opts.update(
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web_safari"],
                        "player_skip": ["configs"],
                    }
                },
            }
        )
        if self.cookies_path:
            opts["cookiefile"] = str(self.cookies_path)
        return opts

    def _get_authenticated_options(self) -> Dict[str, Any]:
        """Nuclear strategy: Full cookies authentication."""
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

    # ── Extraction methods ───────────────────────────────────────────────

    def _fetch_sync(self, url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous yt-dlp extraction (runs in thread pool)."""
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
            return cast(Dict[str, Any], result) if result else {}

    @staticmethod
    def _resolve_stream_url(info: Dict[str, Any]) -> Optional[str]:
        """Ensure we have a usable stream URL, preferring mp4 for OpenCV."""
        stream_url = info.get("url")
        if stream_url:
            return cast(str, stream_url)

        # Fallback: search formats list for best mp4
        formats: List[Dict[str, Any]] = info.get("formats", [])
        mp4_formats = [f for f in formats if f.get("ext") == "mp4" and f.get("url")]
        if mp4_formats:
            best = max(
                mp4_formats,
                key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
            )
            return cast(str, best["url"])

        return None

    async def _try_invidious(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Attempt extraction through Invidious federation with instance rotation.

        Returns:
            Video info dict on success, None if all instances exhausted.
        """
        for attempt in range(self.MAX_INVIDIOUS_ATTEMPTS):
            instance = await _instance_manager.get_instance()
            target_url = f"{instance}/watch?v={video_id}"
            opts = self._get_base_options()

            try:
                logger.info(
                    f"[Invidious {attempt + 1}/{self.MAX_INVIDIOUS_ATTEMPTS}] "
                    f"Trying {instance} for {video_id}"
                )

                loop = asyncio.get_running_loop()

                def _do_fetch(
                    url: str = target_url, options: Dict[str, Any] = opts
                ) -> Dict[str, Any]:
                    return self._fetch_sync(url, options)

                info: Dict[str, Any] = await asyncio.wait_for(
                    loop.run_in_executor(None, _do_fetch),
                    timeout=60,  # Hard timeout for the entire extraction
                )

                # Validate: must have a playable stream URL
                resolved = self._resolve_stream_url(info)
                if resolved:
                    info["url"] = resolved
                    logger.info(f"✓ Invidious success via {instance}")
                    return info
                else:
                    raise ValueError("No mp4-compatible stream URL in extraction result")

            except asyncio.TimeoutError:
                logger.warning(f"✗ Invidious timeout on {instance}")
                _instance_manager.mark_bad(instance)

            except Exception as e:
                logger.warning(f"✗ Invidious failed on {instance}: {e}")
                _instance_manager.mark_bad(instance)

                # Brief jitter between retries
                await asyncio.sleep(random.uniform(0.5, 2.0))

        logger.warning(
            f"Invidious exhausted ({self.MAX_INVIDIOUS_ATTEMPTS} attempts). "
            f"Falling back to direct yt-dlp strategies."
        )
        return None

    def _try_direct_strategies(self, video_url: str) -> Dict[str, Any]:
        """
        Synchronous fallback: try direct yt-dlp strategies in cascade.

        Order: Android → Web Safari → Authenticated (if cookies available)
        """
        strategies: List[Tuple[str, Dict[str, Any]]] = [
            ("Android Client", self._get_android_options()),
            ("Web Safari + PO Token", self._get_web_safari_options()),
        ]

        if self.cookies_path:
            strategies.append(("Authenticated (Cookies)", self._get_authenticated_options()))

        last_error: Optional[Exception] = None

        for strategy_name, options in strategies:
            try:
                logger.info(f"[Direct] Attempting: {strategy_name}")
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    if info:
                        logger.info(f"✓ Direct success with {strategy_name}")
                        return cast(Dict[str, Any], info)

            except yt_dlp.utils.DownloadError as e:
                last_error = e
                error_msg = str(e)

                if "Sign in to confirm" in error_msg or "bot" in error_msg:
                    logger.warning(f"✗ {strategy_name} blocked by anti-bot. Next strategy...")
                elif "429" in error_msg:
                    logger.error("Rate limit (429). Propagating for backoff...")
                    raise
                else:
                    logger.warning(f"✗ {strategy_name} failed: {error_msg}")

                continue

            except Exception as e:
                last_error = e
                logger.warning(f"✗ {strategy_name} error: {e}")
                continue

        logger.critical(f"All direct strategies failed for {video_url}")
        raise last_error or yt_dlp.utils.DownloadError("All extraction strategies exhausted")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type(yt_dlp.utils.DownloadError),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def extract_info(self, video_url: str) -> Dict[str, Any]:
        """
        Extract video metadata and stream info with full fallback cascade.

        Cascade:
        1. Invidious federation (async, run via nested event loop)
        2. yt-dlp Android client
        3. yt-dlp Web Safari + PO Token
        4. yt-dlp with cookies.txt (nuclear)

        Args:
            video_url: YouTube video URL or bare video ID

        Returns:
            Video info dictionary with at minimum 'url', 'duration',
            'chapters', 'heatmap'

        Raises:
            yt_dlp.utils.DownloadError: If ALL strategies fail
        """
        # Normalize input
        if not video_url.startswith("http"):
            video_id = video_url
            video_url = f"https://www.youtube.com/watch?v={video_url}"
        else:
            # Extract video ID from URL
            video_id = video_url.split("v=")[-1].split("&")[0]

        # ── Strategy 1: Invidious Federation ─────────────────────────────
        try:
            # extract_info is called from asyncio.to_thread in the Painter flow,
            # meaning we're in a worker thread with no running event loop.
            # If there IS a running loop (unexpected), spawn a fresh thread.
            try:
                asyncio.get_running_loop()
                # Running inside an existing event loop — spawn a new thread.
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, self._try_invidious(video_id))
                    invidious_result = future.result(timeout=120)
            except RuntimeError:
                # No running loop — safe to use asyncio.run()
                invidious_result = asyncio.run(self._try_invidious(video_id))

            if invidious_result and invidious_result.get("url"):
                return invidious_result

        except Exception as e:
            logger.warning(f"Invidious cascade failed entirely: {e}")

        # ── Strategy 2-4: Direct yt-dlp Fallback ────────────────────────
        logger.info(f"Invidious unavailable. Falling back to direct yt-dlp for {video_id}")
        return self._try_direct_strategies(video_url)

    def extract_heatmap_peaks(
        self, heatmap_data: List[Dict[str, Any]], top_n: int = 5
    ) -> List[float]:
        """
        Extract top N peak timestamps from video heatmap data.

        Args:
            heatmap_data: List of heatmap points with 'start_time' and 'value' keys
            top_n: Number of peaks to return

        Returns:
            List of timestamps (floats) sorted by engagement value (descending)
        """
        if not heatmap_data:
            return []

        valid_points = [p for p in heatmap_data if "value" in p and "start_time" in p]
        sorted_points = sorted(valid_points, key=lambda x: x.get("value", 0), reverse=True)
        return [p.get("start_time", 0.0) for p in sorted_points[:top_n]]
