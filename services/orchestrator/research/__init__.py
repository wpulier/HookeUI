"""Researchers: background subagents (manager.py; the worker process is research_worker/)."""
from .manager import ResearchManager, ResearchRefused, Session

__all__ = ["ResearchManager", "ResearchRefused", "Session"]
