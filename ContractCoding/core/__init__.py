"""Core primitives shared by every other layer.

This package is intentionally minimal: it owns provenance metadata
(`MarginAnnotation`, `AgentRole`) and the cross-team append-only event
stream (`Event`, `EventKind`, `EventLog`). No higher-level concept may be
introduced here — anything that talks about *contracts*, *memory*, or
*agents* must live in its own layer.
"""

from __future__ import annotations

from .margin import AgentRole, MarginAnnotation
from .events import Event, EventKind, EventLog

__all__ = [
    "AgentRole",
    "MarginAnnotation",
    "Event",
    "EventKind",
    "EventLog",
]
