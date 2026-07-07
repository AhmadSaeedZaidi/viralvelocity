import abc
import io
import json
import logging
from datetime import UTC, datetime
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
    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download

    HAS_HF = True
    HAS_PANDAS = True
except ImportError:
    pass

if TYPE_CHECKING:
    try:
        from google.cloud import storage  # noqa: F401
    except ImportError:
        pass

    try:
        import pandas as pd
        from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download
    except ImportError:
        pass

logger = logging.getLogger("atlas.vault")


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
    def store_visual_evidence(self, video_id: str, frames: list[tuple[int, bytes]]) -> None:
        pass

    @abc.abstractmethod
    def store_binary(self, path: str, data: io.BytesIO) -> str:
        pass

    @abc.abstractmethod
    def fetch_binary(self, path: str) -> io.BytesIO | None:
        pass

    def store_metadata(self, video_id: str, data: dict[Any, Any], date: str | None = None) -> None:
        if date is None:
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        path = f"metadata/{date}/{video_id}.json"
        self.store_json(path, data)

    def fetch_metadata(self, video_id: str, date: str) -> dict[Any, Any] | None:
        path = f"metadata/{date}/{video_id}.json"
        return self.fetch_json(path)

    def store_transcript(self, video_id: str, transcript: dict[Any, Any]) -> None:
        path = f"transcripts/{video_id}.json"
        self.store_json(path, transcript)

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
            logger.error(f"HF upload failed for {path}: {e}")
            raise

    def fetch_json(self, path: str) -> dict[Any, Any] | None:
        try:
            local_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=path,
                repo_type="dataset",
                token=self.token,
            )
            with open(local_path) as f:
                result: dict[Any, Any] = json.load(f)
                return result
        except Exception as e:
            logger.warning(f"Failed to fetch {path} from HF vault: {e}")
            return None

    def list_files(self, prefix: str) -> list[str]:
        try:
            files = self.api.list_repo_files(
                repo_id=self.repo_id,
                repo_type="dataset",
            )
            return [f for f in files if f.startswith(prefix)]
        except Exception as e:
            logger.error(f"Failed to list files with prefix {prefix}: {e}")
            return []

    def store_visual_evidence(self, video_id: str, frames: list[tuple[int, bytes]]) -> None:
        """Stores visual frames cleanly using a single commit operation to avoid API rate limits."""
        try:
            operations = []
            for idx, img_bytes in frames:
                path = f"frames/{video_id}/{idx}.jpg"
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
            logger.error(f"Failed to archive visuals for {video_id}: {e}")
            raise

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
            logger.error(f"HF binary upload failed for {path}: {e}")
            raise

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

            with open(local_path, "rb") as f:
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

        path = f"metrics/date={date}/hour={hour}/stats.parquet"

        try:
            existing_df = None
            try:
                local_path = hf_hub_download(
                    repo_id=self.repo_id,
                    filename=path,
                    repo_type="dataset",
                    token=self.token,
                )
                existing_df = pd.read_parquet(local_path)
                logger.info(f"Found existing metrics file with {len(existing_df)} rows")
            except Exception:
                logger.info(f"No existing metrics file at {path}, creating new")

            new_df = pd.DataFrame(data)

            if existing_df is not None:
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                combined_df = new_df

            buffer = io.BytesIO()
            combined_df.to_parquet(buffer, engine="pyarrow", index=False)
            buffer.seek(0)

            self.api.upload_file(
                path_or_fileobj=buffer,
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Append metrics: {len(data)} rows to {path}",
            )

            logger.info(f"Appended {len(data)} metrics to {path} (total: {len(combined_df)})")

        except Exception as e:
            logger.error(f"Failed to append metrics to {path}: {e}")
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
            logger.error(f"GCS upload failed for {path}: {e}")
            raise

    def fetch_json(self, path: str) -> dict[Any, Any] | None:
        try:
            blob = self.bucket.blob(path)
            if not blob.exists():
                return None
            result: dict[Any, Any] = json.loads(blob.download_as_text())
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch {path} from GCS vault: {e}")
            return None

    def list_files(self, prefix: str) -> list[str]:
        try:
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception as e:
            logger.error(f"Failed to list files with prefix {prefix}: {e}")
            return []

    def store_visual_evidence(self, video_id: str, frames: list[tuple[int, bytes]]) -> None:
        """Stores visual frames individually using the frames/ path."""
        try:
            for idx, img_bytes in frames:
                path = f"frames/{video_id}/{idx}.jpg"
                blob = self.bucket.blob(path)
                blob.upload_from_string(img_bytes, content_type="image/jpeg")
            logger.info(f"Stored {len(frames)} frames for {video_id} to GCS")
        except Exception as e:
            logger.error(f"Failed to store visuals for {video_id}: {e}")
            raise

    def store_binary(self, path: str, data: io.BytesIO) -> str:
        try:
            data.seek(0)
            blob = self.bucket.blob(path)
            blob.upload_from_file(data)
            logger.info(f"Stored binary {path} to GCS vault")
            return f"gs://{self.bucket_name}/{path}"
        except Exception as e:
            logger.error(f"GCS binary upload failed for {path}: {e}")
            raise

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

        path = f"metrics/date={date}/hour={hour}/stats.parquet"

        try:
            existing_df = None
            blob = self.bucket.blob(path)
            if blob.exists():
                buffer = io.BytesIO()
                blob.download_to_file(buffer)
                buffer.seek(0)
                existing_df = pd.read_parquet(buffer)
                logger.info(f"Found existing metrics file with {len(existing_df)} rows")
            else:
                logger.info(f"No existing metrics file at {path}, creating new")

            new_df = pd.DataFrame(data)

            if existing_df is not None:
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                combined_df = new_df

            buffer = io.BytesIO()
            combined_df.to_parquet(buffer, engine="pyarrow", index=False)
            buffer.seek(0)

            # Upload
            blob.upload_from_file(buffer, content_type="application/octet-stream")

            logger.info(f"Appended {len(data)} metrics to {path} (total: {len(combined_df)})")

        except Exception as e:
            logger.error(f"Failed to append metrics to {path}: {e}")
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
        if settings.VAULT_PROVIDER == "gcs":
            _vault_instance = GCSVault()
        else:
            _vault_instance = HuggingFaceVault()
    return _vault_instance


def reset_vault() -> None:
    """Reset the vault singleton.  Useful for testing."""
    global _vault_instance
    _vault_instance = None


def __getattr__(name: str) -> Any:
    """Lazy module attribute for backward-compatible ``from atlas.vault import vault``."""
    if name == "vault":
        return get_vault()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    vault: VaultStrategy
