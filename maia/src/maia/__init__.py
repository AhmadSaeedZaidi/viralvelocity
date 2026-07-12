"""Maia — the stateless agent layer for Project Pleiades.

Producers (Hunter, Archeologist) discover videos and push them to the work
queue; consumers (Scribe, Painter, Streamer, Singer, Tracker, Janitor) process
them. All persistence lives in Atlas.
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
