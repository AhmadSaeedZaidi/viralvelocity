"""Smoke tests for the Pleiades MCP server tools.

These exercise the tool wiring and artifact handling without hitting YouTube
or Mistral (network calls are monkeypatched). They verify that tools return the
expected structure and that artifacts are persisted and addressable.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make the local packages importable.
ROOT = Path(__file__).resolve().parents[2]
for p in ("mcp/src", "atlas/src", "maia/src"):
    sys.path.insert(0, str(ROOT / p))

from maia import strategies as strategies_mod  # noqa: E402

import pleiades_mcp.server as server  # noqa: E402

VID = "dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("YOUTUBE_API_KEY_POOL_JSON", '["k1"]')
    monkeypatch.setenv("VAULT_PROVIDER", "huggingface")
    monkeypatch.setenv("HF_DATASET_ID", "mock/ds")
    monkeypatch.setenv("HF_TOKEN", "mock")


def test_search_youtube_parses_and_returns(monkeypatch):
    fake = {"items": [
        {"id": {"videoId": VID},
         "snippet": {"title": "T", "channelTitle": "C", "publishedAt": "2026-01-01T00:00:00Z"}}
    ]}

    class _Strat:
        async def search(self, params):
            return fake

    monkeypatch.setattr(server, "build_search_strategy", lambda **k: _Strat())
    out = asyncio.run(server.search_youtube("cats", max_results=5))
    assert out["count"] == 1
    assert out["results"][0]["video_id"] == VID
    assert out["results"][0]["watch_url"].endswith(VID)


def test_build_search_strategy_uses_reserve_pool(monkeypatch):
    from pleiades_mcp import media
    monkeypatch.setenv("MCP_YOUTUBE_API_KEY_POOL_JSON", '["RES_A","RES_B"]')
    strat = media.build_search_strategy()
    assert strat.keys.pool_name == "mcp-reserve"
    assert strat.keys.keys == ["RES_A", "RES_B"]
    # rotates through both reserve keys, not the shared pool
    assert {strat.keys.next_key(), strat.keys.next_key()} == {"RES_A", "RES_B"}


def test_build_search_strategy_falls_back_without_reserve(monkeypatch):
    from pleiades_mcp import media
    monkeypatch.delenv("MCP_YOUTUBE_API_KEY_POOL_JSON", raising=False)
    # Neutralize the .env fallback so we exercise the true "no reserve" path.
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: {})
    calls = {}

    class _Strat:
        def __init__(self, pool, agent_name=None):
            calls["pool"] = pool

    monkeypatch.setattr(strategies_mod, "YouTubeSearchStrategy", _Strat)
    media.build_search_strategy()
    assert calls["pool"] == "hunting"


def test_get_transcript_file_persists_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)
    monkeypatch.setattr(server, "fetch_transcript_segments",
                        lambda vid: [{"text": "hello world", "start": 0.0, "duration": 1.0}])
    out = server.get_transcript(VID, format="file")
    assert out["video_id"] == VID
    assert out["artifact"].startswith("file://")
    assert tmp_path.joinpath(VID, "transcript").exists()


def test_summarize_transcript_writes_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)
    monkeypatch.setattr(server, "fetch_transcript_segments",
                        lambda vid: [{"text": "a b c", "start": 0.0, "duration": 1.0}])
    monkeypatch.setattr(server, "summarize_transcript_text",
                        lambda segs, **k: "# Summary\n- point one")
    out = server.summarize_transcript(VID)
    assert "Summary" in out["summary"]
    assert out["artifact"].startswith("file://")
    assert tmp_path.joinpath(VID, "summary").exists()


def test_get_keyframes_persists_images(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)
    monkeypatch.setattr(server, "extract_keyframes",
                        lambda vid, d: [(0, b"RIFFxxxxWEBPdata"), (30, b"RIFFxxxxWEBPdata")])
    out = server.get_keyframes(VID, max_frames=2)
    assert out["frame_count"] == 2
    assert all(f["uri"].startswith("file://") for f in out["frames"])


def test_get_keyframes_inline_returns_images(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)
    monkeypatch.setattr(server, "extract_keyframes",
                        lambda vid, d: [(0, b"RIFFxxxxWEBPdata"), (30, b"RIFFxxxxWEBPdata")])
    out = server.get_keyframes(VID, max_frames=2, inline=True)
    assert isinstance(out, list)
    assert out[0]["frame_count"] == 2
    imgs = [x for x in out[1:] if isinstance(x, server.Image)]
    assert len(imgs) == 2


def test_get_thumbnail_inline_and_url(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)
    monkeypatch.setattr(
        server, "fetch_thumbnail",
        lambda vid: (b"\xff\xd8\xffjpegbytes", f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"),
    )
    # inline (default): list with summary dict + one Image
    out = server.get_thumbnail(VID)
    assert isinstance(out, list)
    assert out[0]["video_id"] == VID
    assert out[0]["source_url"].endswith("maxresdefault.jpg")
    assert any(isinstance(x, server.Image) for x in out[1:])
    assert tmp_path.joinpath(VID, "thumbnail").exists()
    # URL-only
    out2 = server.get_thumbnail(VID, inline=False)
    assert isinstance(out2, dict)
    assert out2["artifact"].startswith("file://")


def test_get_thumbnail_unavailable_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)

    def _boom(vid):
        raise server.MediaUnavailableError("no thumb")

    monkeypatch.setattr(server, "fetch_thumbnail", _boom)
    out = server.get_thumbnail(VID)
    assert "error" in out


def test_get_audio_persists_opus(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path)
    server._store = server.ArtifactStore(tmp_path)
    fake = tmp_path / f"{VID}.opus"
    fake.write_bytes(b"fakeopus")
    monkeypatch.setattr(server, "extract_audio_file", lambda vid, d: fake)
    out = server.get_audio(VID)
    assert out["video_id"] == VID
    assert out["artifact"].startswith("file://")


def test_invalid_video_id_errors():
    out = server.get_transcript("not a video id !!")
    assert "error" in out
