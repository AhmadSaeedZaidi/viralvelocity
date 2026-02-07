"""
Agent Registry for dynamic command dispatch.

This module provides a centralized registry of all available Maia agents,
enabling polymorphic command dispatch through the main entry point.
"""

from typing import Dict, Type

from maia.agent import Agent
from maia.archeologist.flow import ArcheologistAgent
from maia.hunter.flow import HunterAgent
from maia.janitor.flow import JanitorAgent
from maia.painter.flow import PainterAgent
from maia.scribe.flow import ScribeAgent
from maia.tracker.flow import TrackerAgent

AGENT_REGISTRY: Dict[str, Type[Agent]] = {
    "hunter": HunterAgent,
    "tracker": TrackerAgent,
    "janitor": JanitorAgent,
    "archeologist": ArcheologistAgent,
    "scribe": ScribeAgent,
    "painter": PainterAgent,
}
