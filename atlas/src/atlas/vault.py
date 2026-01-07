import json
import logging
import io
import abc
from typing import Optional, Any, List, Tuple
from atlas.config import settings

# Conditional imports to avoid hard dependencies if specific extras aren't installed
try:
    from google.cloud import storage
except ImportError:
    storage = None

try:
    from huggingface_hub import HfApi, hf_hub_download
    import pandas as pd # Required for Parquet archiving
except ImportError:
    HfApi = None

logger = logging.getLogger("atlas.vault")

class VaultStrategy(abc.ABC):
    """
    Abstract Base Class for Storage Providers.
    """
    @abc.abstractmethod
    def store_json(self, path: str, data: Any):
        pass

    @abc.abstractmethod
    def fetch_json(self, path: str) -> Optional[dict]:
        pass
    
    @abc.abstractmethod
    def list_files(self, prefix: str) -> List[str]:
        """Lists filenames under a specific prefix"""
        pass

    @abc.abstractmethod
    def store_visual_evidence(self, video_id: str, frames: List[Tuple[int, bytes]]):
        """
        Stores raw image bytes for future training/audit.
        frames = [(frame_index, image_bytes), ...]
        """
        pass

class HuggingFaceVault(VaultStrategy):
    """
    The 'Infinite' Research Vault.
    Uses Git LFS and Parquet for efficient storage.
    """
    def __init__(self):
        if not HfApi:
            raise ImportError("huggingface_hub not installed. Install 'atlas[hf]' or 'atlas[all]'")
        self.repo_id = settings.HF_DATASET_ID
        self.token = settings.HF_TOKEN.get_secret_value() if settings.HF_TOKEN else None
        self.api = HfApi(token=self.token)

    def store_json(self, path: str, data: Any):
        try:
            json_bytes = json.dumps(data).encode('utf-8')
            self.api.upload_file(
                path_or_fileobj=io.BytesIO(json_bytes),
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Vault: Add metadata {path}"
            )
        except Exception as e:
            logger.error(f"HF Upload Failed: {e}")

    def fetch_json(self, path: str) -> Optional[dict]:
        try:
            local_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=path,
                repo_type="dataset",
                token=self.token
            )
            with open(local_path, 'r') as f:
                return json.load(f)
        except Exception:
            return None

    def list_files(self, prefix: str) -> List[str]:
        try:
            # Note: This can be slow on massive repos, filtering is key
            return self.api.list_repo_files(
                repo_id=self.repo_id, 
                repo_type="dataset",
                path=prefix
            )
        except Exception:
            return []

    def store_visual_evidence(self, video_id: str, frames: List[Tuple[int, bytes]]):
        """
        Archives visual evidence as a Parquet file.
        This is ML-native and highly compressible.
        """
        try:
            # Create a DataFrame
            data = [
                {"video_id": video_id, "frame_index": idx, "image": img_bytes} 
                for idx, img_bytes in frames
            ]
            df = pd.DataFrame(data)
            
            # Buffer to Parquet
            buffer = io.BytesIO()
            df.to_parquet(buffer, engine='pyarrow')
            
            # Upload
            path = f"visuals/{video_id}.parquet"
            self.api.upload_file(
                path_or_fileobj=buffer,
                path_in_repo=path,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"Vault: Visual Evidence {video_id}"
            )
            logger.info(f"Archived visual evidence for {video_id} to HF.")
        except Exception as e:
            logger.error(f"Failed to archive visuals: {e}")

class GCSVault(VaultStrategy):
    """
    The Enterprise Vault.
    Uses Google Cloud Storage Buckets.
    """
    def __init__(self):
        if not storage:
            raise ImportError("google-cloud-storage not installed.")
        self.bucket_name = settings.GCS_BUCKET_NAME
        try:
            self.client = storage.Client()
            self.bucket = self.client.bucket(self.bucket_name)
        except Exception as e:
            logger.warning(f"GCS Init Failed: {e}")
            self.client = None

    def store_json(self, path: str, data: Any):
        if not self.client: return
        blob = self.bucket.blob(path)
        blob.upload_from_string(json.dumps(data), content_type="application/json")

    def fetch_json(self, path: str) -> Optional[dict]:
        if not self.client: return None
        blob = self.bucket.blob(path)
        if not blob.exists(): return None
        return json.loads(blob.download_as_text())

    def list_files(self, prefix: str) -> List[str]:
        if not self.client: return []
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
        return [blob.name for blob in blobs]

    def store_visual_evidence(self, video_id: str, frames: List[Tuple[int, bytes]]):
        """
        Stores visuals as individual JPEGs (Standard Object Storage pattern).
        """
        if not self.client: return
        for idx, img_bytes in frames:
            path = f"visuals/{video_id}/{idx}.jpg"
            blob = self.bucket.blob(path)
            blob.upload_from_string(img_bytes, content_type="image/jpeg")

# Factory Logic
def get_vault() -> VaultStrategy:
    if settings.VAULT_PROVIDER == "gcs":
        return GCSVault()
    return HuggingFaceVault()

# Global Singleton
vault = get_vault()