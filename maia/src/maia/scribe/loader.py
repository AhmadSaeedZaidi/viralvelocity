"""Maia Scribe: Transcript loader using cookie-authenticated requests session.

The loader builds a ``requests.Session`` populated with the YouTube cookies
resolved from ``atlas.config.settings.youtube_cookies_resolved_path`` and passes
it into ``YouTubeTranscriptApi(http_client=...)``. This bypasses YouTube's
anti-bot protections for both the transcript listing and content fetch.
"""

import http.cookiejar as cookiejar
import logging
from typing import Any

import requests  # type: ignore[import-untyped]
from prefect import get_run_logger
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import IpBlocked, RequestBlocked, TranscriptsDisabled

from maia.utils import RateLimitError


class TranscriptExtractionError(Exception):
    """Raised when transcript extraction fails or returns empty data."""


class TranscriptLoader:
    """
    Wrapper for ``youtube-transcript-api`` 1.x with cookie authentication.

    Cookie path is resolved exclusively through ``atlas.config.settings`` at
    construction time. Direct ``os.environ`` access is forbidden.
    """

    def __init__(self, cookies_path: str | None = None) -> None:
        """Initialise loader.

        Args:
            cookies_path: Explicit override for cookie file path.
                          When *None* (default) the path is resolved from
                          ``atlas.config.settings.youtube_cookies_resolved_path``.
        """
        try:
            self.logger: Any = get_run_logger()
        except Exception:
            self.logger = logging.getLogger("maia.scribe.loader")

        if cookies_path is not None:
            resolved: str | None = cookies_path
        else:
            from atlas.config import settings

            resolved = settings.youtube_cookies_resolved_path

        self.cookies_path: str | None = resolved
        self._http_client = self._build_http_client(resolved)

        if not resolved:
            self.logger.warning(
                "TranscriptLoader: No cookies configured. "
                "YouTube may rate-limit or block unauthenticated transcript requests."
            )

    @staticmethod
    def _build_http_client(cookies_path: str | None) -> requests.Session:
        """Build a ``requests.Session`` with optional cookies and a browser UA."""
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        if cookies_path:
            try:
                jar = cookiejar.MozillaCookieJar(cookies_path)
                jar.load(ignore_discard=True, ignore_expires=True)
                session.cookies = jar  # type: ignore[assignment]
            except Exception as exc:
                logging.getLogger("maia.scribe.loader").warning(
                    f"TranscriptLoader: Failed to load cookies from {cookies_path}: {exc}"
                )

        return session

    def fetch(self, video_id: str) -> list[dict[Any, Any]]:
        """Fetch transcript for a YouTube video using authenticated session.

        Returns:
            Non-empty list of transcript segments (``{"text", "start", "duration"}``).

        Raises:
            RateLimitError: On YouTube 429 / TooManyRequests / IpBlocked.
            TranscriptExtractionError: When no transcript data can be obtained.
            TranscriptsDisabled: When the video has captions disabled.
        """
        try:
            api = YouTubeTranscriptApi(http_client=self._http_client)
            transcript_list = api.list(video_id)

            try:
                transcript = transcript_list.find_manually_created_transcript(["en"])
            except Exception:
                try:
                    transcript = transcript_list.find_generated_transcript(["en"])
                except Exception:
                    transcript = transcript_list.find_manually_created_transcript(
                        ["es", "fr", "de", "pt", "ru", "ja", "ko"]
                    )

            fetched = transcript.fetch()
            result: list[dict[Any, Any]] = [
                {"text": s.text, "start": s.start, "duration": s.duration} for s in fetched.snippets
            ]
            if not result:
                raise TranscriptExtractionError(
                    f"Transcript fetch returned empty data for {video_id}"
                )
            return result

        except (IpBlocked, RequestBlocked) as exc:
            self.logger.critical(
                f"YouTube blocked transcript request for {video_id}: {type(exc).__name__}"
            )
            raise RateLimitError(f"429/Block (Scribe) — YouTube {type(exc).__name__}") from exc

        except TranscriptsDisabled:
            raise

        except TranscriptExtractionError:
            raise

        except Exception as e:
            raise TranscriptExtractionError(
                f"Failed to extract transcript for {video_id}: {e}"
            ) from e
