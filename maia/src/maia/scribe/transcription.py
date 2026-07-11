"""Transcription orchestration.

Two distinct consumers of YouTube speech:

* **Scribe** — captions only. It reads official/auto captions via the shared
  ``StealthVideoStreamer`` (``transcribe_video`` → :class:`TranscriptLoader`).
  It never downloads audio, so there is no STT-quota cost and no risk of
  colliding with the streamer's single audio fetch.

* **Singer** — audio only. It transcribes audio that the *streamer* producer has
  already extracted and stored in the vault. ``transcribe_audio_path`` takes a
  local audio file (fetched back from the vault) and runs the Grok → Mistral
  cascade. Keeping audio extraction in one place (the streamer) means every
  video is fetched from YouTube exactly once for its audio track.

All paths return the vault schema ``[{text, start, duration}]`` and raise the
scribe's shared exceptions so the flows' existing handlers apply:
  * :class:`TranscriptRateLimitError` → release the video to PENDING (retry later)
  * :class:`TranscriptExtractionError` → genuinely no transcript (mark done)
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maia.scribe.audio import AudioExtractionError, download_to_tempfile
from maia.scribe.grok import (
    GrokRateLimitError,
    GrokTranscriber,
    GrokTranscriptionError,
)
from maia.scribe.loader import (
    TranscriptExtractionError,
    TranscriptLoader,
    TranscriptRateLimitError,
)
from maia.scribe.mistral import (
    MistralRateLimitError,
    MistralTranscriber,
    MistralTranscriptionError,
    TranscriptionUnavailableError,
)

logger = logging.getLogger(__name__)


@dataclass
class TranscriptResult:
    """Result of a transcription attempt."""

    segments: list[dict[str, Any]]
    source: str  # "captions" | "mistral" | "grok"
    audio_bytes: bytes | None = None
    audio_ext: str = "opus"


# ── Local-audio (already extracted to disk) ──────────────────────────────────


def _transcribe_via_mistral_path(audio_path: Path) -> TranscriptResult:
    """Transcribe a local audio file with Voxtral."""
    segments = MistralTranscriber().transcribe(audio_path)
    return TranscriptResult(segments, "mistral")


def _transcribe_via_grok_path(audio_path: Path) -> TranscriptResult:
    """Transcribe a local audio file with Grok STT."""
    segments = GrokTranscriber().transcribe(audio_path)
    return TranscriptResult(segments, "grok")


def transcribe_audio_path(
    audio_path: Path,
    strategy: str | None = None,
    language: str | None = None,
) -> TranscriptResult:
    """Transcribe a *local* audio file according to the configured strategy.

    Used by the **singer** consumer, which fetches the audio the streamer
    already stored in the vault and writes it to a temp file before calling
    this. Strategy (``settings.SCRIBE_TRANSCRIBER``):

      * ``grok``    — audio → Grok STT only.
      * ``mistral`` — audio → Voxtral only.
      * ``auto``     — audio → Grok, falling back to audio → Voxtral.

    Raises:
        TranscriptRateLimitError: all available paths were rate-limited.
        TranscriptExtractionError: no transcript could be produced.
    """
    from atlas.config import get_settings

    settings = get_settings()
    strategy = strategy or settings.SCRIBE_TRANSCRIBER

    if strategy == "mistral":
        return _mistral_only_path(audio_path)
    if strategy == "grok":
        return _grok_only_path(audio_path)

    # auto: Grok first, Mistral fallback.
    try:
        return _transcribe_via_grok_path(audio_path)
    except GrokRateLimitError:
        logger.info("audio→Grok rate-limited, falling back to audio→Mistral")
    except (TranscriptionUnavailableError, GrokTranscriptionError):
        pass

    try:
        return _transcribe_via_mistral_path(audio_path)
    except TranscriptionUnavailableError:
        raise TranscriptExtractionError(
            "No audio transcriber available for local audio"
        ) from None
    except MistralRateLimitError as e:
        raise TranscriptRateLimitError(f"Mistral rate-limited: {e}") from e
    except (AudioExtractionError, MistralTranscriptionError) as e:
        raise TranscriptExtractionError(f"Audio transcription failed: {e}") from e


def _mistral_only_path(audio_path: Path) -> TranscriptResult:
    try:
        return _transcribe_via_mistral_path(audio_path)
    except TranscriptionUnavailableError as e:
        raise TranscriptExtractionError(str(e)) from e
    except MistralRateLimitError as e:
        raise TranscriptRateLimitError(f"Mistral rate-limited: {e}") from e
    except (AudioExtractionError, MistralTranscriptionError) as e:
        raise TranscriptExtractionError(f"Audio transcription failed: {e}") from e


def _grok_only_path(audio_path: Path) -> TranscriptResult:
    try:
        return _transcribe_via_grok_path(audio_path)
    except TranscriptionUnavailableError as e:
        raise TranscriptExtractionError(str(e)) from e
    except GrokRateLimitError as e:
        raise TranscriptRateLimitError(f"Grok rate-limited: {e}") from e
    except (AudioExtractionError, GrokTranscriptionError) as e:
        raise TranscriptExtractionError(f"Audio transcription failed: {e}") from e


# ── Captions-only (Scribe) ───────────────────────────────────────────────────


def transcribe_video(
    video_id: str,
    strategy: str | None = None,
    store_audio: bool | None = None,
) -> TranscriptResult:
    """Transcribe *video_id* via official/auto **captions** only (Scribe).

    The Scribe is captions-only by design: audio is extracted and stored
    separately by the streamer producer, then transcribed by the singer
    consumer. This keeps the YouTube audio fetch in one place and makes the
    Scribe free of STT-quota cost / rate limits.

    Raises:
        TranscriptRateLimitError: captions were throttled on every client.
        TranscriptExtractionError: no captions available for this video.
    """
    from atlas.config import get_settings

    settings = get_settings()
    strategy = strategy or settings.SCRIBE_TRANSCRIBER

    if strategy not in ("captions", "auto", "mistral", "grok"):
        logger.warning(f"Unknown SCRIBE_TRANSCRIBER={strategy!r}; using captions")
    # Captions cascade across player clients; returns [{text, start, duration}].
    return TranscriptResult(TranscriptLoader().fetch(video_id), "captions")


# Keep the YouTube-audio-download path available for ad-hoc/standalone use
# (e.g. the manually-runnable muralist capability). It mirrors the local-audio
# cascade but first downloads the audio via the shared streamer.
def transcribe_audio_download(
    video_id: str,
    strategy: str | None = None,
    store_audio: bool = False,
) -> TranscriptResult:
    """Download *video_id*'s audio then run the Grok→Mistral cascade.

    Used only by standalone tooling that is not part of the streamer/singer
    fleet (the fleet stores audio via the streamer instead).
    """
    from atlas.config import get_settings

    settings = get_settings()
    strategy = strategy or settings.SCRIBE_TRANSCRIBER

    audio_path, tmpdir = download_to_tempfile(video_id)
    try:
        if strategy in ("captions",):
            raise TranscriptExtractionError("captions strategy has no audio path")
        if strategy == "mistral":
            result = _mistral_only_path(audio_path)
        elif strategy == "grok":
            result = _grok_only_path(audio_path)
        else:
            result = transcribe_audio_path(audio_path)
        if store_audio:
            result.audio_bytes = Path(audio_path).read_bytes()
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
