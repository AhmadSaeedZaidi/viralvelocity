"""
Maia - The Stateless Agent Layer for Project Pleiades

Maia is a Prefect-based agent system for video discovery, monitoring,
archival, and media processing. It operates as a stateless layer that
interfaces exclusively with Atlas for all persistence.

Architecture (Producer-Consumer Pipeline):

  Producers (identify targets, push to work queue):
  - Hunter:      Discovery & Ingestion (YouTube search + Snowball sampling)
  - Archeologist: Historical Curation (Grave Robbery method)

  Consumers (pull from work queue, process, update status):
  - Scribe:      Transcription (yt-dlp native subtitle extraction)
  - Painter:     Visual Archival (Intelligent keyframe extraction)
  - Tracker:     Velocity Monitoring (3-Zone Defense strategy)
  - Janitor:     Tiered storage cleanup

Core Principles:
    - Stateless: All state persists in Atlas
    - Repository Pattern: Database access via atlas.repositories.*
    - Strategy Pattern: YouTube Data API access via YouTubeSearchStrategy
    - Producer-Consumer: No direct coupling between discovery and processing
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
