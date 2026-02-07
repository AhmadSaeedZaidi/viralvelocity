"""
Maia - The Stateless Agent Layer for Project Pleiades

Maia is a Prefect-based agent system for video discovery, monitoring,
archival, and media processing. It operates as a stateless layer that
interfaces exclusively with Atlas for all persistence.

Architecture:
- Hunter: Discovery & Ingestion (YouTube search + Snowball sampling)
- Tracker: Velocity Monitoring (3-Zone Defense strategy)
- Archeologist: Historical Curation (Grave Robbery method)
- Scribe: Transcription (youtube-transcript-api wrapper)
- Painter: Visual Archival (Intelligent keyframe extraction)

Core Principles:
- Stateless: All state persists in Atlas
- DAO Pattern: Database access via atlas.adapters.maia.MaiaDAO only
- Resiliency Strategy: Rate limit = immediate container suicide for IP rotation
"""

__version__ = "0.1.0"
__author__ = "Ahmad Saeed Zaidi"
__license__ = "MIT"

from maia.archeologist import ArcheologistAgent, run_archeology_campaign
from maia.hunter import HunterAgent, run_hunter_cycle
from maia.janitor import JanitorAgent
from maia.painter import PainterAgent, run_painter_cycle
from maia.scribe import ScribeAgent, run_scribe_cycle
from maia.tracker import TrackerAgent, run_tracker_cycle

__all__ = [
    "HunterAgent",
    "TrackerAgent",
    "JanitorAgent",
    "ArcheologistAgent",
    "ScribeAgent",
    "PainterAgent",
    "run_hunter_cycle",
    "run_tracker_cycle",
    "run_archeology_campaign",
    "run_scribe_cycle",
    "run_painter_cycle",
    "__version__",
]
