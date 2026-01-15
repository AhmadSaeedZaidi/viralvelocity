import logging
from typing import Dict, List, Optional

from prefect import get_run_logger
from youtube_transcript_api import TooManyRequests, TranscriptsDisabled, YouTubeTranscriptApi


class TranscriptLoader:
    """
    Wrapper for youtube-transcript-api to handle proxy rotation logic.
    """

    def __init__(self):
        # We try to get the prefect logger, fallback to standard if running outside flow context
        try:
            self.logger = get_run_logger()
        except Exception:
            self.logger = logging.getLogger("maia.scribe.loader")

    def fetch(self, video_id: str) -> Optional[List[Dict]]:
        """
        Fetches transcript.
        Implements Hydra Protocol: If blocked (TooManyRequests), raises SystemExit.
        """
        try:
            # We fetch the list object to inspect available transcripts
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # Priority 1: Manual English
            try:
                transcript = transcript_list.find_manually_created_transcript(["en"])
            except:
                # Priority 2: Generated English (Better than nothing)
                try:
                    transcript = transcript_list.find_generated_transcript(["en"])
                except:
                    # Priority 3: Any Manual (Foreign language is better than no text)
                    # We look for common languages.
                    transcript = transcript_list.find_manually_created_transcript(
                        ["es", "fr", "de", "pt", "ru", "ja", "ko"]
                    )

            # Fetch the actual data
            return transcript.fetch()

        except TooManyRequests:
            self.logger.critical(
                f"IP BLOCKED by YouTube (TooManyRequests). Initiating Hydra Protocol for Scribe."
            )
            # CRITICAL: This kills the container to force a rotation
            raise SystemExit("429 Rate Limit (Scribe) - Container Suicide")

        except TranscriptsDisabled:
            # This is a valid state (video has no captions), return None so we can mark it 'unavailable'
            return None

        except Exception as e:
            # Network glitches or video deleted during processing
            self.logger.warning(f"Error fetching transcript for {video_id}: {e}")
            return None
