import logging
from typing import Any, Dict, List, Optional

from prefect import get_run_logger
from youtube_transcript_api import (  # type: ignore[attr-defined]
    TooManyRequests,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from maia.utils import RateLimitError


class TranscriptExtractionError(Exception):
    """Raised when transcript extraction fails or returns empty data."""


class TranscriptLoader:
    """
    Wrapper for youtube-transcript-api to handle proxy rotation logic.
    """

    def __init__(self) -> None:
        # We try to get the prefect logger, fallback to standard if running outside flow context
        try:
            self.logger = get_run_logger()
        except Exception:
            self.logger = logging.getLogger("maia.scribe.loader")

    def fetch(self, video_id: str) -> List[Dict[Any, Any]]:
        """Fetch transcript for a YouTube video.

        Returns:
            Non-empty list of transcript segments.

        Raises:
            RateLimitError: On YouTube 429 / TooManyRequests.
            TranscriptExtractionError: When no transcript data can be obtained.
            TranscriptsDisabled: When the video has captions disabled.
        """
        try:
            # We fetch the list object to inspect available transcripts
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)  # type: ignore[attr-defined]

            # Priority 1: Manual English
            try:
                transcript = transcript_list.find_manually_created_transcript(["en"])
            except Exception:
                # Priority 2: Generated English (Better than nothing)
                try:
                    transcript = transcript_list.find_generated_transcript(["en"])
                except Exception:
                    # Priority 3: Any Manual (Foreign language is better than no text)
                    # We look for common languages.
                    transcript = transcript_list.find_manually_created_transcript(
                        ["es", "fr", "de", "pt", "ru", "ja", "ko"]
                    )

            # Fetch the actual data
            result: List[Dict[Any, Any]] = transcript.fetch()
            if not result:
                raise TranscriptExtractionError(
                    f"Transcript fetch returned empty data for {video_id}"
                )
            return result

        except TooManyRequests:
            self.logger.critical("IP BLOCKED by YouTube (TooManyRequests). Raising RateLimitError.")
            raise RateLimitError("429 Rate Limit (Scribe) — YouTube TooManyRequests")

        except TranscriptsDisabled:
            raise

        except TranscriptExtractionError:
            raise

        except Exception as e:
            raise TranscriptExtractionError(
                f"Failed to extract transcript for {video_id}: {e}"
            ) from e
