import abc
import contextlib
import io
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atlas.config import settings

HAS_GCS = False
try:
    from google.cloud import storage  # type: ignore
    from google.cloud.storage import Client as GCSClient  # type: ignore  # noqa: F401

    HAS_GCS = True
except ImportError:
    pass

HAS_HF = False
HAS_PANDAS = False
try:
    import pandas as pd
    from huggingface_hub import (
        CommitOperationAdd,
        CommitOperationDelete,
        HfApi,
        hf_hub_download,
    )
    from huggingface_hub.errors import EntryNotFoundError

    HAS_HF = True
    HAS_PANDAS = True
except ImportError:
    pass

if TYPE_CHECKING:
    with contextlib.suppress(ImportError):
        from google.cloud import storage  # noqa: F401

    try:
        import pandas as pd
        from huggingface_hub import (
            CommitOperationAdd,
            CommitOperationDelete,
            HfApi,
            hf_hub_download,
        )
    except ImportError:
        pass

logger = logging.getLogger("atlas.vault")


def _is_rate_limited(exc: Exception) -> bool:
    """Heuristic detection of HTTP 429 (rate-limit) from a vault SDK error."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status == 429:
        return True
    return "429" in str(exc)


class VaultStrategy(abc.ABC):
    @abc.abstractmethod
    def store_json(self, path: str, data: Any) -> None:
        pass

    @abc.abstractmethod
    def fetch_json(self, path: str) -> dict[Any, Any] | None:
        pass

    @abc.abstractmethod
    def list_files(self, prefix: str) -> list[str]:
        pass

    @abc.abstractmethod
    def store_visual_evidence(
        self, video_id: str, frames: list[tuple[int, bytes]], ext: str = "webp"
    ) -> None:
        """Store keyframes for one video in a single commit."""

    @abc.abstractmethod
    def store_visual_evidence_batch(
        self, entries: list[tuple[str, list[tuple[int, bytes]], str]]
    ) -> None:
        """Store keyframes for MANY videos in a SINGLE commit.

        ``entries`` is a list of ``(video_id, frames, ext)`` tuples. Bundling
        multiple videos into one HuggingFace commit keeps us far under the
        128-commits/hour account cap during bulk recollection.
        """
        pass

    @abc.abstractmethod
    def store_binary(self, path: str, data: io.BytesIO) -> str:
        pass

    @abc.abstractmethod
    def store_batch(self, items: list[tuple[str, Any]]) -> list[str]:
        """Write many files in a single commit (HF) or batched call (GCS)."""
        pass

    @abc.abstractmethod
    def fetch_binary(self, path: str) -> io.BytesIO | None:
        pass

    @abc.abstractmethod
    def make_uri(self, path: str) -> str:
        """Return the provider-qualified URI for a stored *path*."""
        pass

    @abc.abstractmethod
    def delete_files(self, paths: list[str]) -> int:
        """Permanently delete the given repo-relative ``paths``. Returns count deleted."""
        pass

    def store_metadata(self, video_id: str, data: dict[Any, Any], date: str | None = None) -> None:
        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        path = f"metadata/{date}/{video_id}.json"
        self.store_json(path, data)

    def fetch_metadata(self, video_id: str, date: str) -> dict[Any, Any] | None:
        path = f"metadata/{date}/{video_id}.json"
        return self.fetch_json(path)

    def fetch_transcript(self, video_id: str) -> dict[Any, Any] | None:
        path = f"transcripts/{video_id}.json"
        return self.fetch_json(path)

    @abc.abstractmethod
    def append_metrics(
        self,
        data: list[dict[Any, Any]],
        date: str | None = None,
        hour: str | None = None,
    ) -> None:
        pass


class HuggingFaceVault(VaultStrategy):
    def __init__(self) -> None:
        if not HAS_HF:
            raise ImportError(
                "HuggingFace dependencies not installed. Install with: pip install huggingface-hub"
            )
        if not settings.HF_DATASET_ID:
            raise ValueError("HF_DATASET_ID required for HuggingFace vault")

        self.repo_id = settings.HF_DATASET_ID
        self.token = settings.HF_TOKEN.get_secret_value() if settings.HF_TOKEN else None

        self.api = HfApi(token=self.token)

    def store_json(self, path: str, data: Any) -> None:
        try:
            json_bytes = json.dumps(data).encode("utf-8")
            self.api.upload_file(
                path_or_fileobj=io.BytesIO(json_bytes),
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Vault: Add metadata {path}",
            )
            logger.info(f"Stored {path} to HF vault")
        except Exception as e:
            logger.exception(f"HF upload failed for {path}: {e}")
            raise

    def fetch_json(self, path: str) -> dict[Any, Any] | None:
        try:
            local_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=path,
                repo_type="dataset",
                token=self.token,
            )
            with Path(local_path).open() as f:
                result: dict[Any, Any] = json.load(f)
                return result
        except EntryNotFoundError:
            logger.info(f"File not found in HF vault: {path}")
            return None
        except Exception as e:
            logger.exception(f"Failed to fetch {path} from HF vault: {e}")
            raise

    def list_files(self, prefix: str) -> list[str]:
        try:
            files = self.api.list_repo_files(
                repo_id=self.repo_id,
                repo_type="dataset",
            )
            return [f for f in files if f.startswith(prefix)]
        except Exception as e:
            logger.exception(f"Failed to list files with prefix {prefix}: {e}")
            return []

    def store_visual_evidence(
        self, video_id: str, frames: list[tuple[int, bytes]], ext: str = "webp"
    ) -> None:
        """Stores visual frames cleanly using a single commit operation to avoid API rate limits."""
        try:
            operations = []
            for idx, img_bytes in frames:
                path = f"frames/{video_id}/{idx}.{ext}"
                operations.append(CommitOperationAdd(path_in_repo=path, path_or_fileobj=img_bytes))

            if operations:
                self.api.create_commit(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    operations=operations,
                    commit_message=f"Vault: Visual Evidence {video_id} ({len(frames)} frames)",
                )
                logger.info(f"Archived {len(frames)} frames for {video_id} to HF")
            else:
                logger.warning(f"No frames provided to archive for {video_id}")
        except Exception as e:
            logger.exception(f"Failed to archive visuals for {video_id}: {e}")
            raise

    def store_visual_evidence_batch(
        self, entries: list[tuple[str, list[tuple[int, bytes]], str]]
    ) -> None:
        """Store keyframes for many videos in a single HF commit."""
        operations = []
        frame_count = 0
        for video_id, frames, ext in entries:
            for idx, img_bytes in frames:
                path = f"frames/{video_id}/{idx}.{ext}"
                operations.append(CommitOperationAdd(path_in_repo=path, path_or_fileobj=img_bytes))
                frame_count += 1
        if not operations:
            logger.warning("store_visual_evidence_batch: no frames provided")
            return
        try:
            self.api.create_commit(
                repo_id=self.repo_id,
                repo_type="dataset",
                operations=operations,
                commit_message=(
                    f"Vault: Visual Evidence batch "
                    f"({len(entries)} videos, {frame_count} frames)"
                ),
            )
            logger.info(
                f"Archived {frame_count} frames for {len(entries)} videos to HF in one commit"
            )
        except Exception as e:
            logger.exception(f"Failed to archive visual batch to HF: {e}")
            raise

    def make_uri(self, path: str) -> str:
        return f"hf://datasets/{self.repo_id}/{path}"

    def delete_files(self, paths: list[str]) -> int:
        """Delete the given repo-relative ``paths`` in batched commits.

        Chunked (500/commit) so a large purge stays well under HuggingFace's
        128-commits/hour account cap. Returns the number of files deleted.
        """
        if not paths:
            return 0
        total = 0
        chunk_size = 500
        for i in range(0, len(paths), chunk_size):
            batch = paths[i : i + chunk_size]
            ops = [CommitOperationDelete(path_in_repo=p) for p in batch]
            try:
                self.api.create_commit(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    operations=ops,
                    commit_message=f"Vault: purge {len(batch)} files",
                )
                total += len(batch)
                logger.info(f"Purged {len(batch)} files from HF vault")
            except Exception as e:
                logger.exception(f"HF purge failed: {e}")
                raise
        return total

    def store_binary(self, path: str, data: io.BytesIO) -> str:
        try:
            data.seek(0)
            self.api.upload_file(
                path_or_fileobj=data,
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Vault: Binary {path}",
            )
            logger.info(f"Stored binary {path} to HF vault")
            return f"hf://datasets/{self.repo_id}/{path}"
        except Exception as e:
            logger.exception(f"HF binary upload failed for {path}: {e}")
            raise

    def store_batch(
        self, items: list[tuple[str, Any]], max_attempts: int = 8, base_delay: float = 30.0
    ) -> list[str]:
        """Write many files in a SINGLE commit (avoids the 128 commits/hour limit).

        Retries internally on HTTP 429 (HuggingFace's account-wide commit cap),
        backing off exponentially, so callers do not need their own retry
        wrapper. ``items`` is a list of ``(path_in_repo, data)`` where ``data``
        is either a JSON-serialisable object or an ``io.BytesIO`` (binary).

        Returns the list of vault URIs, in input order.
        """
        if not items:
            return []
        operations = []
        for path, data in items:
            if isinstance(data, io.BytesIO):
                data.seek(0)
                payload: Any = data
            else:
                payload = io.BytesIO(json.dumps(data).encode("utf-8"))
            operations.append(CommitOperationAdd(path_in_repo=path, path_or_fileobj=payload))
        delay = base_delay
        last_exc: Any = None
        for attempt in range(1, max_attempts + 1):
            try:
                self.api.create_commit(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    operations=operations,
                    commit_message=f"Vault: batch write ({len(items)} files)",
                )
                logger.info(f"Stored {len(items)} files to HF vault in one commit")
                return [f"hf://datasets/{self.repo_id}/{p}" for p, _ in items]
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if _is_rate_limited(e):
                    logger.warning(
                        f"Vault 429 (rate limited) — backing off {delay:.0f}s "
                        f"(attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 600.0)
                else:
                    logger.exception(f"HF batch upload failed: {e}")
                    time.sleep(min(base_delay * attempt, 120.0))
        raise last_exc

    def fetch_binary(self, path: str) -> io.BytesIO | None:
        try:
            if path.startswith("hf://"):
                path = path.split(self.repo_id + "/")[-1]

            local_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=path,
                repo_type="dataset",
                token=self.token,
            )

            with Path(local_path).open("rb") as f:
                return io.BytesIO(f.read())

        except Exception as e:
            logger.warning(f"Failed to fetch binary {path} from HF vault: {e}")
            return None

    def append_metrics(
        self,
        data: list[dict[Any, Any]],
        date: str | None = None,
        hour: str | None = None,
    ) -> None:
        """
        Append time-series metrics to partitioned Parquet files.

        Each call writes an **individual batch file** (timestamped) rather than
        reading, concatenating, and rewriting the accumulated file.  This avoids:
        - **Lost updates** — concurrent calls each write their own file instead
          of racing on a single shared file.
        - **O(n²) overhead** — every append previously rewrote every row written
          so far; now each batch is exactly its own size.
        """
        if not data:
            logger.warning("No metrics data to append")
            return

        if not HAS_PANDAS:
            raise ImportError("Pandas required for metrics")

        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        if hour is None:
            hour = datetime.now(UTC).strftime("%H")

        batch_ts = datetime.now(UTC).strftime("%H%M%S_%f")
        path = f"metrics/date={date}/hour={hour}/{batch_ts}.parquet"

        try:
            buffer = io.BytesIO()
            new_df = pd.DataFrame(data)
            new_df.to_parquet(buffer, engine="pyarrow", index=False)
            buffer.seek(0)

            self.api.upload_file(
                path_or_fileobj=buffer,
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Append metrics: {len(data)} rows to {path}",
            )

            logger.info(f"Appended {len(data)} metrics to {path}")

        except Exception as e:
            logger.exception(f"Failed to append metrics to {path}: {e}")
            raise


class GCSVault(VaultStrategy):
    def __init__(self) -> None:
        if not HAS_GCS:
            raise ImportError(
                "Google Cloud Storage not installed. Install with: pip install google-cloud-storage"
            )
        if not settings.GCS_BUCKET_NAME:
            raise ValueError("GCS_BUCKET_NAME required for GCS vault")

        self.bucket_name = settings.GCS_BUCKET_NAME
        self.client = storage.Client()
        self.bucket = self.client.bucket(self.bucket_name)

    def store_json(self, path: str, data: Any) -> None:
        try:
            blob = self.bucket.blob(path)
            blob.upload_from_string(json.dumps(data), content_type="application/json")
            logger.info(f"Stored {path} to GCS vault")
        except Exception as e:
            logger.exception(f"GCS upload failed for {path}: {e}")
            raise

    def fetch_json(self, path: str) -> dict[Any, Any] | None:
        try:
            blob = self.bucket.blob(path)
            if not blob.exists():
                return None
            result: dict[Any, Any] = json.loads(blob.download_as_text())
            return result
        except Exception as e:
            logger.exception(f"Failed to fetch {path} from GCS vault: {e}")
            raise

    def list_files(self, prefix: str) -> list[str]:
        try:
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception as e:
            logger.exception(f"Failed to list files with prefix {prefix}: {e}")
            return []

    def store_visual_evidence(
        self, video_id: str, frames: list[tuple[int, bytes]], ext: str = "webp"
    ) -> None:
        """Stores visual frames individually using the frames/ path."""
        try:
            for idx, img_bytes in frames:
                path = f"frames/{video_id}/{idx}.{ext}"
                blob = self.bucket.blob(path)
                blob.upload_from_string(img_bytes, content_type=f"image/{ext}")
            logger.info(f"Stored {len(frames)} frames for {video_id} to GCS")
        except Exception as e:
            logger.exception(f"Failed to store visuals for {video_id}: {e}")
            raise

    def store_visual_evidence_batch(
        self, entries: list[tuple[str, list[tuple[int, bytes]], str]]
    ) -> None:
        """Store keyframes for many videos (GCS: one upload per blob)."""
        try:
            count = 0
            for video_id, frames, ext in entries:
                for idx, img_bytes in frames:
                    path = f"frames/{video_id}/{idx}.{ext}"
                    blob = self.bucket.blob(path)
                    blob.upload_from_string(img_bytes, content_type=f"image/{ext}")
                    count += 1
            logger.info(f"Stored {count} frames for {len(entries)} videos to GCS")
        except Exception as e:
            logger.exception(f"Failed to store visual batch to GCS: {e}")
            raise

    def make_uri(self, path: str) -> str:
        return f"gs://{self.bucket_name}/{path}"

    def delete_files(self, paths: list[str]) -> int:
        if not paths:
            return 0
        count = 0
        for p in paths:
            blob = self.bucket.blob(p)
            if blob.exists():
                blob.delete()
                count += 1
        logger.info(f"Purged {count} files from GCS vault")
        return count

    def store_binary(self, path: str, data: io.BytesIO) -> str:
        try:
            data.seek(0)
            blob = self.bucket.blob(path)
            blob.upload_from_file(data)
            logger.info(f"Stored binary {path} to GCS vault")
            return f"gs://{self.bucket_name}/{path}"
        except Exception as e:
            logger.exception(f"GCS binary upload failed for {path}: {e}")
            raise

    def store_batch(
        self, items: list[tuple[str, Any]], max_attempts: int = 8, base_delay: float = 5.0
    ) -> list[str]:
        """Write many files in one logical batch (GCS: individual blob uploads).

        Retries internally on HTTP 429 (GCS per-project write cap)."""
        delay = base_delay
        last_exc: Any = None
        for attempt in range(1, max_attempts + 1):
            try:
                uris = []
                for path, data in items:
                    if isinstance(data, io.BytesIO):
                        data.seek(0)
                        blob = self.bucket.blob(path)
                        blob.upload_from_file(data)
                    else:
                        blob = self.bucket.blob(path)
                        blob.upload_from_string(
                            json.dumps(data), content_type="application/json"
                        )
                    uris.append(f"gs://{self.bucket_name}/{path}")
                logger.info(f"Stored {len(items)} files to GCS vault")
                return uris
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if _is_rate_limited(e):
                    time.sleep(delay)
                    delay = min(delay * 2, 120.0)
                else:
                    time.sleep(min(base_delay * attempt, 30.0))
        raise last_exc

    def fetch_binary(self, path: str) -> io.BytesIO | None:
        try:
            if path.startswith("gs://"):
                path = path.split(self.bucket_name + "/")[-1]

            blob = self.bucket.blob(path)
            if not blob.exists():
                return None

            buffer = io.BytesIO()
            blob.download_to_file(buffer)
            buffer.seek(0)
            return buffer
        except Exception as e:
            logger.warning(f"Failed to fetch binary {path} from GCS vault: {e}")
            return None

    def append_metrics(
        self,
        data: list[dict[Any, Any]],
        date: str | None = None,
        hour: str | None = None,
    ) -> None:
        """
        Append time-series metrics to partitioned Parquet files in GCS.

        Each call writes an **individual batch file** (timestamped) rather than
        reading, concatenating, and rewriting the accumulated file.  This avoids
        lost updates (race on a single shared file) and O(n²) rewrite overhead.
        """
        if not data:
            logger.warning("No metrics data to append")
            return

        if not HAS_PANDAS:
            raise ImportError(
                "pandas required for metrics append. Install: pip install pandas pyarrow"
            )

        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        if hour is None:
            hour = datetime.now(UTC).strftime("%H")

        batch_ts = datetime.now(UTC).strftime("%H%M%S_%f")
        path = f"metrics/date={date}/hour={hour}/{batch_ts}.parquet"

        try:
            buffer = io.BytesIO()
            new_df = pd.DataFrame(data)
            new_df.to_parquet(buffer, engine="pyarrow", index=False)
            buffer.seek(0)

            blob = self.bucket.blob(path)
            blob.upload_from_file(buffer, content_type="application/octet-stream")

            logger.info(f"Appended {len(data)} metrics to {path}")

        except Exception as e:
            logger.exception(f"Failed to append metrics to {path}: {e}")
            raise


_vault_instance: VaultStrategy | None = None


def get_vault() -> VaultStrategy:
    """Get or create the vault singleton.

    Instantiation is deferred until the first call so that importing
    ``atlas.vault`` does not trigger environment-variable validation or
    network calls (useful for testing).
    """
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = GCSVault() if settings.VAULT_PROVIDER == "gcs" else HuggingFaceVault()
    return _vault_instance


def reset_vault() -> None:
    """Reset the vault singleton.  Useful for testing."""
    global _vault_instance
    _vault_instance = None


def audio_path(video_id: str) -> str:
    """Return the repo-relative vault path for a video's extracted audio."""
    return f"audio/{video_id}.opus"


def meta_path(video_id: str) -> str:
    """Return the repo-relative vault path for a video's stored yt-dlp metadata."""
    return f"meta/{video_id}.info.json"


def video_path(video_id: str, ext: str = "mp4") -> str:
    """Return the repo-relative vault path for a video's archived source clip."""
    return f"videos/{video_id}.{ext}"


def __getattr__(name: str) -> Any:
    """Lazy module attribute for backward-compatible ``from atlas.vault import vault``."""
    if name == "vault":
        return get_vault()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    vault: VaultStrategy
