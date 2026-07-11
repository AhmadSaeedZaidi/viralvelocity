"""Purge short / low-quality videos from the database and the vault.

The Hunter quality gate now stops *new* Shorts / AI-slop at ingestion, but the
corpus may already contain low-value clips. This command removes videos shorter
than a duration threshold (default 3 minutes) — including any artifacts they
already produced (audio, frames, full video, transcript, metadata JSON).

Overwrite-on-reprocess: video IDs are the primary key everywhere and every vault
artifact path is derived deterministically from the video ID
(``audio/{id}.opus``, ``frames/{id}/...``, ``videos/{id}.mp4``,
``transcripts/{id}.json``, ``metadata/{date}/{id}.json``). So if a purged video
is rediscovered, re-ingestion recreates the DB row (``ON CONFLICT DO UPDATE``)
and the agents overwrite the *same* vault paths in place — no duplicate writes,
exactly the "overwrite the bits" behaviour requested. The purge simply clears
the stale low-quality copy first.
"""

import logging
from typing import Any

from atlas.repositories import VideoRepository
from atlas.vault import audio_path, get_vault, video_path

logger = logging.getLogger(__name__)

METADATA_PREFIX = "metadata/"
TRANSCRIPTS_PREFIX = "transcripts/"
FRAMES_PREFIX = "frames/"
AUDIO_PREFIX = "audio/"
VIDEO_PREFIX = "videos/"


def collect_artifact_paths(
    vault: Any,
    video_ids: list[str],
    metadata_files: list[str] | None = None,
) -> list[str]:
    """Return every *existing* vault path associated with the given video IDs.

    Only paths that are actually present in the vault are returned, so the
    subsequent delete does not 404 on artifacts that were never stored (e.g. a
    video with no extracted audio). Existence is checked per prefix via
    ``list_files`` rather than probing each speculative path.
    """
    paths: list[str] = []

    existing_audio = set(vault.list_files(AUDIO_PREFIX))
    existing_video = set(vault.list_files(VIDEO_PREFIX))
    existing_transcripts = set(vault.list_files(TRANSCRIPTS_PREFIX))
    for vid in video_ids:
        a = audio_path(vid)
        if a in existing_audio:
            paths.append(a)
        v = video_path(vid)
        if v in existing_video:
            paths.append(v)
        t = f"{TRANSCRIPTS_PREFIX}{vid}.json"
        if t in existing_transcripts:
            paths.append(t)

    frame_prefixes = {f"{FRAMES_PREFIX}{vid}/" for vid in video_ids}
    if frame_prefixes:
        paths.extend(
            f
            for f in vault.list_files(FRAMES_PREFIX)
            if any(f.startswith(p) for p in frame_prefixes)
        )

    meta_suffixes = {f"{vid}.json" for vid in video_ids}
    if metadata_files is None:
        metadata_files = vault.list_files(METADATA_PREFIX)
    paths.extend(
        f
        for f in metadata_files
        if f.startswith(METADATA_PREFIX) and f.split("/")[-1] in meta_suffixes
    )

    return paths


async def purge_short_videos(
    min_duration: int = 180,
    dry_run: bool = True,
    delete_artifacts: bool = True,
) -> dict[str, Any]:
    """Remove videos shorter than ``min_duration`` seconds.

    Args:
        min_duration: Drop videos with ``duration < min_duration``.
        dry_run: When True (default), only report what *would* be removed.
        delete_artifacts: When True, also delete vault artifacts for those IDs.

    Returns a summary dict (counts + the affected IDs).
    """
    repo = VideoRepository()
    ids = await repo.find_short_video_ids(min_duration)

    result: dict[str, Any] = {
        "min_duration": min_duration,
        "dry_run": dry_run,
        "video_count": len(ids),
    }
    if not ids:
        logger.info("Purge: nothing to remove (no videos shorter than %ss).", min_duration)
        return result

    result["ids"] = ids

    if dry_run:
        logger.info(
            "Purge DRY-RUN: would remove %d videos shorter than %ss.",
            len(ids),
            min_duration,
        )
        return result

    vault = get_vault()
    if delete_artifacts:
        metadata_files = vault.list_files(METADATA_PREFIX)
        paths = collect_artifact_paths(vault, ids, metadata_files)
        artifacts_deleted = vault.delete_files(paths) if paths else 0
        result["artifacts_deleted"] = artifacts_deleted
        logger.info("Purge: deleted %d vault artifact files.", artifacts_deleted)

    removed = await repo.delete_videos(ids)
    result["videos_deleted"] = removed
    logger.info("Purge: deleted %d video rows from the database.", removed)
    return result


async def purge_transcripts(
    scope: str = "without_visuals",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Reset (uncheck) transcripts for a scoped set of videos.

    This is the "uncheck / mark-as-unprocessed + organic overwrite" reset, *not*
    a destructive delete. The videos are returned to ``PENDING`` with
    ``has_transcript = FALSE``; the transcript *rows are left in place*. When the
    Scribe re-claims them it ``record_transcript`` upserts (``ON CONFLICT DO
    UPDATE``), overwriting the stale content in place — no fragile
    delete-then-insert and no window where data is nulled before being rewritten.

    Scopes:
      - ``all``            : every video with a transcript.
      - ``without_visuals``: transcript present but no stored visuals (default).
      - ``without_audio``  : transcript present but no stored audio.
      - ``pending``        : transcript present but still PENDING (unvetted).

    Args:
        scope: Which transcripts to reset (see above).
        dry_run: When True (default), only report what *would* be reset.

    Returns a summary dict (scope, counts, affected IDs).
    """
    repo = VideoRepository()
    ids = await repo.find_transcript_video_ids(scope)

    result: dict[str, Any] = {
        "scope": scope,
        "dry_run": dry_run,
        "transcript_count": len(ids),
    }
    if not ids:
        logger.info("Transcript purge: nothing to reset for scope=%s.", scope)
        return result

    result["ids"] = ids

    if dry_run:
        logger.info(
            "Transcript purge DRY-RUN: would reset %d transcripts (scope=%s).",
            len(ids),
            scope,
        )
        return result

    reset = await repo.unmark_transcripts_batch(ids)
    result["transcripts_reset"] = reset
    logger.info("Transcript purge: reset %d transcripts (scope=%s).", reset, scope)
    return result
