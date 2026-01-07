import abc
import io
import json
import logging
from typing import Any, List, Optional, Tuple

from atlas.config import settings

try:
    from google.cloud import storage
    from google.cloud.storage import Client as GCSClient
except ImportError:
    storage = None
    GCSClient = None

try:
    import pandas as pd
    from huggingface_hub import HfApi, hf_hub_download
except ImportError:
    HfApi = None
    pd = None

logger = logging.getLogger("atlas.vault")

class VaultStrategy(abc.ABC):
    @abc.abstractmethod
    def store_json(self, path: str, data: Any) -> None:
        pass

    @abc.abstractmethod
    def fetch_json(self, path: str) -> Optional[dict]:
        pass
    
    @abc.abstractmethod
    def list_files(self, prefix: str) -> List[str]:
        pass

    @abc.abstractmethod
    def store_visual_evidence(self, video_id: str, frames: List[Tuple[int, bytes]]) -> None:
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

    def fetch_json(self, path: str) -> Optional[dict]:
        try:
            local_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=path,
                repo_type="dataset",
                token=self.token,
            )
            with open(local_path, "r") as f:
                return json.load(f)
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

    def store_visual_evidence(self, video_id: str, frames: List[Tuple[int, bytes]]) -> None:
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
            blob.upload_from_string(
                json.dumps(data), 
                content_type="application/json"
            )
            logger.info(f"Stored {path} to GCS vault")
        except Exception as e:
            logger.error(f"GCS upload failed for {path}: {e}")
            raise

    def fetch_json(self, path: str) -> Optional[dict]:
        try:
            blob = self.bucket.blob(path)
            if not blob.exists():
                return None
            return json.loads(blob.download_as_text())
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

    def store_visual_evidence(self, video_id: str, frames: List[Tuple[int, bytes]]) -> None:
        try:
            for idx, img_bytes in frames:
                path = f"visuals/{video_id}/{idx}.jpg"
                blob = self.bucket.blob(path)
                blob.upload_from_string(img_bytes, content_type="image/jpeg")
            logger.info(f"Stored {len(frames)} frames for {video_id} to GCS")
        except Exception as e:
            logger.error(f"Failed to store visuals for {video_id}: {e}")
            raise

def get_vault() -> VaultStrategy:
    if settings.VAULT_PROVIDER == "gcs":
        return GCSVault()
    return HuggingFaceVault()


vault = get_vault()