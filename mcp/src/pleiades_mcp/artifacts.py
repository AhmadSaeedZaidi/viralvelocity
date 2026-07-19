"""Local artifact store for the Pleiades MCP server.

Tools produce artifacts (transcripts, audio, keyframe images, summaries) that an
LLM client may want to download. Rather than pushing everything through the MCP
protocol as base64 blobs, we persist artifacts to a local directory and return
stable ``file://`` URIs (plus a ``content_type`` and ``size_bytes``) so the
client can fetch them out of band. When the server is launched with
``--http`` it also exposes a static file server on the same port so remote
clients can retrieve artifacts over HTTP.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any

logger = logging.getLogger("pleiades_mcp.artifacts")


class ArtifactStore:
    """FileSystem-backed store keyed by ``<video_id>/<kind>[/<suffix>]``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, video_id: str, kind: str, suffix: str | None = None) -> Path:
        safe_id = _safe(video_id)
        d = self.root / safe_id / kind
        d.mkdir(parents=True, exist_ok=True)
        name = suffix or "artifact"
        return d / name

    def write_bytes(
        self,
        video_id: str,
        kind: str,
        data: bytes,
        suffix: str = "bin",
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Persist raw bytes and return an artifact descriptor dict."""
        path = self._path(video_id, kind, f"{suffix}")
        path.write_bytes(data)
        ctype = content_type or (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        return _describe(path, ctype, len(data))

    def write_text(
        self,
        video_id: str,
        kind: str,
        text: str,
        suffix: str = "txt",
        content_type: str = "text/plain; charset=utf-8",
    ) -> dict[str, Any]:
        """Persist text content and return an artifact descriptor dict."""
        path = self._path(video_id, kind, suffix)
        path.write_text(text, encoding="utf-8")
        return _describe(path, content_type, path.stat().st_size)

    def resolve(self, uri: str) -> Path | None:
        """Resolve a stored ``file://`` URI back to a Path if it lives in our root."""
        if not uri.startswith("file://"):
            return None
        p = Path(uri[len("file://") :])
        try:
            p.resolve().relative_to(self.root.resolve())
        except ValueError:
            return None
        return p if p.exists() else None


def _describe(path: Path, content_type: str, size: int) -> dict[str, Any]:
    return {
        "uri": f"file://{path.resolve()}",
        "path": str(path.resolve()),
        "content_type": content_type,
        "size_bytes": size,
    }


def _safe(video_id: str) -> str:
    vid = video_id.strip()
    if not vid or any(c in vid for c in "/\\.."):
        # hash to avoid path traversal / empty ids
        return hashlib.sha1(vid.encode()).hexdigest()[:16]
    return vid
