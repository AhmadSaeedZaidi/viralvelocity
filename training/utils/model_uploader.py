import os
from datetime import datetime

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError


class ModelUploader:
    """
    Handles uploading and archiving of model artifacts to the Hugging Face Hub.
    Uses environment variables HF_USERNAME and HF_MODELS to construct the repository ID.
    """

    def __init__(self, repo_id=None):
        """
        Initialize the uploader.

        Args:
            repo_id (str, optional): Explicit repository ID.
                If None, constructs it from env vars.
        """
        self.api = HfApi()
        self.token = os.getenv("HF_TOKEN")

        if repo_id:
            self.repo_id = repo_id
        else:
            username = os.getenv("HF_USERNAME")
            model_repo = os.getenv("HF_MODELS")
            if not username or not model_repo:
                raise ValueError(
                    "HF_USERNAME and HF_MODELS must be set in environment variables."
                )
            self.repo_id = f"{username}/{model_repo}"

    def _archive_existing_file(self, path_in_repo):
        """
        Internal helper: Checks if a file exists on the Hub.
        If yes, downloads it and re-uploads it to 'archive/{path}-{timestamp}'.
        """
        print(f"Checking for existing file to archive: {path_in_repo}...")

        try:
            # Attempt to download the existing file from the Hub
            # We use a temporary local directory to store the old file
            temp_download_dir = "/tmp/viralvelocity_archive_buffer"
            os.makedirs(temp_download_dir, exist_ok=True)

            local_old_file = hf_hub_download(
                repo_id=self.repo_id,
                filename=path_in_repo,
                token=self.token,
                local_dir=temp_download_dir,
                # Ensure we get a real file, not a symlink
                local_dir_use_symlinks=False,
            )

            # Construct the archive path
            # Structure: archive/{original_folder}/{filename}-{YYYYMMDD-HHMMSS}.{ext}
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            directory, filename = os.path.split(path_in_repo)
            name, ext = os.path.splitext(filename)

            archive_filename = f"{name}-{timestamp}{ext}"
            archive_path_in_repo = os.path.join("archive", directory, archive_filename)

            print(f"Existing file found. Archiving to: {archive_path_in_repo}")

            # Re-upload to the archive location
            self.api.upload_file(
                path_or_fileobj=local_old_file,
                path_in_repo=archive_path_in_repo,
                repo_id=self.repo_id,
                repo_type="model",
                token=self.token,
                commit_message=f"Archive previous version of {filename}",
            )

            # Clean up the temp file to save space
            try:
                os.remove(local_old_file)
            except OSError:
                pass

        except (EntryNotFoundError, RepositoryNotFoundError):
            # This is normal for the very first run
            print("No existing file found (or repo new). Skipping archive step.")
        except Exception as e:
            print(f"Warning: Could not archive existing file. Error: {str(e)}")
            print("Continuing with overwrite...")

    def upload_file(self, local_path, path_in_repo):
        """
        Uploads a file to HF Hub.
        Automatically archives the previous version if it exists.

        Args:
            local_path (str): Path to the local file to upload.
            path_in_repo (str): Destination path in the repository.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        # 1. Archive the old one
        self._archive_existing_file(path_in_repo)

        # 2. Upload the new one
        print(f"Uploading new model {local_path} to {self.repo_id}/{path_in_repo}...")

        self.api.upload_file(
            path_or_fileobj=local_path,
            path_in_repo=path_in_repo,
            repo_id=self.repo_id,
            repo_type="model",
            token=self.token,
            commit_message=f"Update model {path_in_repo}",
        )

        print("Upload complete.")

    def upload_reports(self, reports, folder="reports"):
        """Upload multiple report files to the Hub.

        Args:
            reports (dict): Mapping of report-name -> local path.
            folder (str): Repo folder to upload reports into.
        """
        for name, local_path in reports.items():
            if not os.path.exists(local_path):
                print(f"Warning: Report {local_path} not found. Skipping.")
                continue

            # Construct path in repo
            # Standardize naming: {name}_latest.html
            filename = f"{name}_latest.html"
            path_in_repo = f"{folder}/{filename}"

            # Use the standard upload logic (which handles archiving)
            self.upload_file(local_path, path_in_repo)
