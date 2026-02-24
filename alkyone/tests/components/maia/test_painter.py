"""
Integration tests for Maia Painter.

Verifies end-to-end behavior using REAL external services:
1. Real YouTube Video (Blender 4.0 Tutorial - B0J27sf9N1Y)
2. Real Network Calls (Invidious federation → yt-dlp fallback)
3. Real Vault Storage

Usage: pytest -m integration tests/integration/test_painter.py
"""

import asyncio
import logging
import os

import pytest
import pytest_asyncio
import yt_dlp
from maia.painter.flow import run_painter_cycle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest_asyncio.fixture
async def dao(fresh_db):
    """Provide MaiaDAO instance for testing with real vault."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_painter_real_full_cycle_blender_tutorial(dao):
    """
    Test the complete Painter cycle on a real video (B0J27sf9N1Y).

    Verifies:
    1. Pre-flight: Video actually has chapters/heatmap (via StealthVideoStreamer).
    2. Execution: Painter agent runs successfully.
    3. Outcome: Video is marked as 'has_visuals' in the DB.

    The StealthVideoStreamer tries Invidious federation first, then falls back
    to direct yt-dlp strategies automatically.
    """
    video_id = "B0J27sf9N1Y"

    # 1. Pre-flight Check using StealthVideoStreamer
    print(f"\n[Test] Verifying metadata for {video_id}...")
    try:
        from maia.painter.streamer import StealthVideoStreamer

        streamer = StealthVideoStreamer()
        # extract_info now tries Invidious first, then direct yt-dlp
        # Run with timeout to prevent hanging
        info = await asyncio.wait_for(
            asyncio.to_thread(streamer.extract_info, video_id), timeout=30.0
        )

        has_chapters = len(info.get("chapters", []) or []) > 0
        has_heatmap = len(info.get("heatmap", []) or []) > 0

        print(
            f"[Test] Video '{info.get('title')}' - Chapters: {has_chapters}, Heatmap: {has_heatmap}"
        )

        if not has_chapters and not has_heatmap:
            pytest.fail("Real video B0J27sf9N1Y no longer has required metadata.")

    except asyncio.TimeoutError:
        pytest.fail("Pre-flight check timed out after 30s - network or service issue")
    except yt_dlp.utils.DownloadError as e:
        error_str = str(e)
        if "429" in error_str:
            pytest.fail("YouTube Rate Limit (429) active during pre-flight check.")
        elif "Sign in" in error_str or "bot" in error_str:
            pytest.fail(
                "All extraction strategies blocked (Invidious + direct). "
                "YouTube anti-bot is active across all federated instances."
            )
        raise

    # 2. Setup DB State
    video_data = {
        "id": {"videoId": video_id},
        "snippet": {
            "channelId": info.get("channel_id", "unknown"),
            "title": info.get("title", "Test Video"),
            "publishedAt": "2023-11-16T00:00:00Z",
            "tags": ["blender"],
            "categoryId": "27",
            "defaultLanguage": "en",
        },
    }
    await dao.ingest_video_metadata(video_data)

    # Reset state from potential previous runs
    await dao._execute(
        "UPDATE videos SET has_visuals = FALSE, status = 'PENDING' WHERE id = %s", (video_id,)
    )

    # 3. Run Agent
    print("[Test] Running Painter Agent...")
    try:
        await run_painter_cycle(batch_size=1)
    except Exception as e:
        pytest.fail(f"Painter Agent crashed during execution: {e}")

    # 4. Assertions - Database State
    print("[Test] Verifying Database State...")
    video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))

    assert video["status"] != "FAILED", f"Video processing failed. Status: {video.get('status')}"
    assert video["has_visuals"] is True, "Painter finished but 'has_visuals' is not True."

    # 5. CRITICAL: Verify REAL HuggingFace Upload
    print("[Test] Verifying REAL HuggingFace Upload...")
    from atlas.config import settings
    from huggingface_hub import HfApi

    if settings.VAULT_PROVIDER == "huggingface":
        hf_token = os.getenv("HF_TOKEN")
        hf_dataset = os.getenv("HF_DATASET_ID")

        assert hf_token, "HF_TOKEN must be set for integration tests"
        assert hf_dataset, "HF_DATASET_ID must be set for integration tests"

        api = HfApi(token=hf_token)

        # Check if visual evidence files exist in the REAL HuggingFace dataset
        try:
            files_in_repo = api.list_repo_files(repo_id=hf_dataset, repo_type="dataset")
            visual_files = [f for f in files_in_repo if f.startswith(f"frames/{video_id}/")]

            assert len(visual_files) > 0, (
                f"CRITICAL FAILURE: No files found in HuggingFace for video {video_id}! "
                f"Vault is NOT writing to real infrastructure. "
                f"Found files: {visual_files}"
            )

            print(
                f"[Test] ✅ SUCCESS! Found {len(visual_files)} frames in REAL HuggingFace dataset:"
            )
            for f in visual_files[:3]:  # Show first 3
                print(f"  - {f}")

            # Track for cleanup
            from alkyone.fixtures import track_hf_upload

            for f in visual_files:
                track_hf_upload(f)

        except Exception as e:
            pytest.fail(f"Failed to verify HuggingFace upload: {e}")

    print("[Test] Success! Real video processed and verified in REAL infrastructure.")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_painter_handles_real_404_video(dao):
    """
    Test resiliency against a non-existent video ID.
    Verifies the system marks FAILED gracefully without crashing.
    """
    fake_id = "v_INVALID_ID_99"

    video_data = {
        "id": {"videoId": fake_id},
        "snippet": {"title": "Invalid Video", "publishedAt": "2024-01-01T00:00:00Z"},
    }
    await dao.ingest_video_metadata(video_data)

    await run_painter_cycle(batch_size=1)

    video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", (fake_id,))
    assert video["status"] == "FAILED", "Invalid video should be marked FAILED"
