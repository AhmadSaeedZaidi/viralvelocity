import abc
import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from atlas.config import settings

try:
    from google.cloud import storage  # type: ignore[import-not-found,import-untyped]
    from google.cloud.storage import (  # type: ignore[import-not-found,import-untyped]
        Client as GCSClient,
    )
except ImportError:
    storage = None
    GCSClient = None

try:
    import pandas as pd  # type: ignore[import-untyped]
    from huggingface_hub import (  # type: ignore[import-not-found,import-untyped]
        HfApi,
        hf_hub_download,
    )
except ImportError:
    HfApi = None
    pd = None

logger = logging.getLogger("atlas.vault")


class VaultStrategy(abc.ABC):
    @abc.abstractmethod
    def store_json(self, path: str, data: Any) -> None:
        pass

    @abc.abstractmethod
    def fetch_json(self, path: str) -> Optional[Dict[Any, Any]]:
        pass

    @abc.abstractmethod
    def list_files(self, prefix: str) -> List[str]:
        pass

    @abc.abstractmethod
    def store_visual_evidence(
        self, video_id: str, frames: List[Tuple[int, bytes]]
    ) -> None:
        pass

    @abc.abstractmethod
    def store_binary(self, path: str, data: io.BytesIO) -> str:
        pass

    @abc.abstractmethod
    def fetch_binary(self, path: str) -> Optional[io.BytesIO]:
        pass

    def store_metadata(
        self, video_id: str, data: Dict[Any, Any], date: Optional[str] = None
    ) -> None:
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        path = f"metadata/{date}/{video_id}.json"
        self.store_json(path, data)

    def fetch_metadata(self, video_id: str, date: str) -> Optional[Dict[Any, Any]]:
        path = f"metadata/{date}/{video_id}.json"
        return self.fetch_json(path)

    def store_transcript(self, video_id: str, transcript: Dict[Any, Any]) -> None:
        path = f"transcripts/{video_id}.json"
        self.store_json(path, transcript)

    def fetch_transcript(self, video_id: str) -> Optional[Dict[Any, Any]]:
        path = f"transcripts/{video_id}.json"
        return self.fetch_json(path)

    @abc.abstractmethod
    def append_metrics(
        self,
        data: List[Dict[Any, Any]],
        date: Optional[str] = None,
        hour: Optional[str] = None,
    ) -> None:
        pass


class HuggingFaceVault(VaultStrategy):
    def __init__(self) -> None:
        if not HfApi or not pd:
            raise ImportError(
                "HuggingFace dependencies not installed. "
                "Install with: pip install huggingface-hub pandas pyarrow"
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

    def fetch_json(self, path: str) -> Optional[Dict[Any, Any]]:
        try:
            local_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=path,
                repo_type="dataset",
                token=self.token,
            )
            with open(local_path, "r") as f:
                result: Dict[Any, Any] = json.load(f)
                return result
        except Exception as e:
            logger.warning(f"Failed to fetch {path} from HF vault: {e}")
            return None

    def list_files(self, prefix: str) -> List[str]:
        try:
            files = self.api.list_repo_files(
                repo_id=self.repo_id,
                repo_type="dataset",
            )
            return [f for f in files if f.startswith(prefix)]
        except Exception as e:
            logger.error(f"Failed to list files with prefix {prefix}: {e}")
            return []

    def store_visual_evidence(
        self, video_id: str, frames: List[Tuple[int, bytes]]
    ) -> None:
        try:
            data = [
                {"video_id": video_id, "frame_index": idx, "image": img_bytes}
                for idx, img_bytes in frames
            ]
            df = pd.DataFrame(data)

            buffer = io.BytesIO()
            df.to_parquet(buffer, engine="pyarrow")
            buffer.seek(0)

            path = f"visuals/{video_id}.parquet"
            self.api.upload_file(
                path_or_fileobj=buffer,
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Vault: Visual Evidence {video_id}",
            )
            logger.info(f"Archived visual evidence for {video_id} to HF")
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

    def fetch_binary(self, path: str) -> Optional[io.BytesIO]:
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
        data: List[Dict[Any, Any]],
        date: Optional[str] = None,
        hour: Optional[str] = None,
    ) -> None:
        """
        Append time-series metrics to partitioned Parquet files.

        Uses Hive-style partitioning: metrics/date=YYYY-MM-DD/hour=HH/stats.parquet
        """
        if not data:
            logger.warning("No metrics data to append")
            return

        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        if hour is None:
            hour = datetime.utcnow().strftime("%H")

        path = f"metrics/date={date}/hour={hour}/stats.parquet"

        try:
            # Try to fetch existing file
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

            # Create DataFrame from new data
            new_df = pd.DataFrame(data)

            # Concat if existing data found
            if existing_df is not None:
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                combined_df = new_df

            # Write to buffer
            buffer = io.BytesIO()
            combined_df.to_parquet(buffer, engine="pyarrow", index=False)
            buffer.seek(0)

            # Upload
            self.api.upload_file(
                path_or_fileobj=buffer,
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Append metrics: {len(data)} rows to {path}",
            )

            logger.info(
                f"Appended {len(data)} metrics to {path} (total: {len(combined_df)})"
            )

        except Exception as e:
            logger.error(f"Failed to append metrics to {path}: {e}")
            raise


class GCSVault(VaultStrategy):
    def __init__(self) -> None:
        if not storage or not GCSClient:
            raise ImportError(
                "Google Cloud Storage not installed. "
                "Install with: pip install google-cloud-storage"
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

    def fetch_json(self, path: str) -> Optional[Dict[Any, Any]]:
        try:
            blob = self.bucket.blob(path)
            if not blob.exists():
                return None
            result: Dict[Any, Any] = json.loads(blob.download_as_text())
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch {path} from GCS vault: {e}")
            return None

    def list_files(self, prefix: str) -> List[str]:
        try:
            blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception as e:
            logger.error(f"Failed to list files with prefix {prefix}: {e}")
            return []

    def store_visual_evidence(
        self, video_id: str, frames: List[Tuple[int, bytes]]
    ) -> None:
        try:
            for idx, img_bytes in frames:
                path = f"visuals/{video_id}/{idx}.jpg"
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

    def fetch_binary(self, path: str) -> Optional[io.BytesIO]:
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
        data: List[Dict[Any, Any]],
        date: Optional[str] = None,
        hour: Optional[str] = None,
    ) -> None:
        """
        Append time-series metrics to partitioned Parquet files in GCS.

        Uses Hive-style partitioning: metrics/date=YYYY-MM-DD/hour=HH/stats.parquet
        """
        if not data:
            logger.warning("No metrics data to append")
            return

        if not pd:
            raise ImportError(
                "pandas required for metrics append. Install: pip install pandas pyarrow"
            )

        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        if hour is None:
            hour = datetime.utcnow().strftime("%H")

        path = f"metrics/date={date}/hour={hour}/stats.parquet"

        try:
            # Try to fetch existing file
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

            # Create DataFrame from new data
            new_df = pd.DataFrame(data)

            # Concat if existing data found
            if existing_df is not None:
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                combined_df = new_df

            # Write to buffer
            buffer = io.BytesIO()
            combined_df.to_parquet(buffer, engine="pyarrow", index=False)
            buffer.seek(0)

            # Upload
            blob.upload_from_file(buffer, content_type="application/octet-stream")

            logger.info(
                f"Appended {len(data)} metrics to {path} (total: {len(combined_df)})"
            )

        except Exception as e:
            logger.error(f"Failed to append metrics to {path}: {e}")
            raise


def get_vault() -> VaultStrategy:
    if settings.VAULT_PROVIDER == "gcs":
        return GCSVault()
    return HuggingFaceVault()


vault = get_vault()
