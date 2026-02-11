"""
Integration tests for Maia Painter.

Verifies end-to-end behavior using REAL external services:
1. Real YouTube Video (Blender 4.0 Tutorial - B0J27sf9N1Y)
2. Real Network Calls (yt-dlp)
3. Real Vault Storage

Usage: pytest -m integration tests/integration/test_painter.py
"""

import asyncio
import logging
import socket

import pytest
import yt_dlp
from maia.painter.flow import run_painter_cycle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_network_available() -> bool:
    """Check if network is available for real integration tests."""
    try:
        socket.create_connection(("www.youtube.com", 80), timeout=3)
        return True
    except OSError:
        return False


@pytest.fixture
async def dao():
    """Provide MaiaDAO instance for testing."""
    from atlas.adapters.maia import MaiaDAO

    dao_instance = MaiaDAO()
    yield dao_instance


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(120)
@pytest.mark.skipif(not is_network_available(), reason="Network unavailable")
async def test_painter_real_full_cycle_blender_tutorial(dao):
    """
    Test the complete Painter cycle on a real video (B0J27sf9N1Y).

    Verifies:
    1. Pre-flight: Video actually has chapters/heatmap (via yt-dlp).
    2. Execution: Painter agent runs successfully.
    3. Outcome: Video is marked as 'has_visuals' in the DB.
    """
    video_id = "B0J27sf9N1Y"

    # 1. Pre-flight Check
    # Ensure the target video currently meets test requirements (has chapters/heatmap).
    print(f"\n[Test] Verifying metadata for {video_id}...")
    ydl_opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

            has_chapters = len(info.get("chapters", []) or []) > 0
            has_heatmap = len(info.get("heatmap", []) or []) > 0

            print(
                f"[Test] Video '{info.get('title')}' - Chapters: {has_chapters}, Heatmap: {has_heatmap}"
            )

            if not has_chapters and not has_heatmap:
                pytest.skip("Real video B0J27sf9N1Y no longer has required metadata. Skipping.")
        except Exception as e:
            if "HTTP Error 429" in str(e):
                pytest.skip("YouTube Rate Limit (429) active.")
            raise e

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

    # 4. Assertions
    print("[Test] Verifying Database State...")
    video = await dao._fetch_one("SELECT * FROM videos WHERE id = %s", (video_id,))

    assert video["status"] != "FAILED", f"Video processing failed. Status: {video.get('status')}"
    assert video["has_visuals"] is True, "Painter finished but 'has_visuals' is not True."

    print("[Test] Success! Real video processed and marked safe.")


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
