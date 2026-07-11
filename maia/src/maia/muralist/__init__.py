"""Maia Muralist: full-video extraction agent ("super painter").

Unused in the active fleet (no systemd unit). It exists as a proven capability:
given a video, it downloads the source video at a compact native resolution
(no re-encode) and can archive it to the vault. Intended for future large-scale
video hoarding once storage + compute are available.
"""

from maia.muralist.flow import MuralistAgent, muralist_flow

__all__ = ["MuralistAgent", "muralist_flow"]
