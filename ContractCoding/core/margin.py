"""Provenance / margin annotation primitives.

Implements design constraint **C7 Margin Provenance**: every artifact in the
registry carries who-authored / what-evidence / how-uncertain metadata.
Inspired by the AI Co-Mathematician "margin notes" mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from typing import Any, Dict, List, Optional


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    STEWARD = "steward"
    PLANNER = "planner"
    INSPECTOR = "inspector"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    JUDGE = "judge"
    USER = "user"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class MarginAnnotation:
    """Per-claim provenance carried alongside every registry write."""

    author_agent: str
    author_role: AgentRole = AgentRole.UNKNOWN
    team_id: str = ""
    source_evidence: List[str] = field(default_factory=list)
    uncertainty: float = 0.0
    parent_event_id: Optional[str] = None
    user_steered: bool = False
    created_at: float = field(default_factory=time.time)
    annotation_id: str = field(default_factory=lambda: f"margin:{uuid.uuid4().hex[:12]}")
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.author_role, str):
            try:
                self.author_role = AgentRole(self.author_role)
            except ValueError:
                self.author_role = AgentRole.UNKNOWN
        try:
            self.uncertainty = float(self.uncertainty)
        except (TypeError, ValueError):
            self.uncertainty = 0.0
        self.uncertainty = max(0.0, min(1.0, self.uncertainty))

    def to_record(self) -> Dict[str, Any]:
        return {
            "annotation_id": self.annotation_id,
            "author_agent": self.author_agent,
            "author_role": self.author_role.value,
            "team_id": self.team_id,
            "source_evidence": list(self.source_evidence),
            "uncertainty": self.uncertainty,
            "parent_event_id": self.parent_event_id,
            "user_steered": bool(self.user_steered),
            "created_at": self.created_at,
            "details": dict(self.details),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "MarginAnnotation":
        payload = dict(payload or {})
        return cls(
            annotation_id=str(payload.get("annotation_id", "") or f"margin:{uuid.uuid4().hex[:12]}"),
            author_agent=str(payload.get("author_agent", "")),
            author_role=AgentRole(str(payload.get("author_role", "unknown")) or "unknown"),
            team_id=str(payload.get("team_id", "")),
            source_evidence=[str(v) for v in payload.get("source_evidence", []) or []],
            uncertainty=float(payload.get("uncertainty", 0.0) or 0.0),
            parent_event_id=payload.get("parent_event_id"),
            user_steered=bool(payload.get("user_steered", False)),
            created_at=float(payload.get("created_at", time.time())),
            details=dict(payload.get("details", {}) or {}),
        )

    @classmethod
    def system(cls, author: str = "system") -> "MarginAnnotation":
        """Cheap factory for non-LLM, deterministic system writes."""
        return cls(author_agent=author, author_role=AgentRole.SYSTEM, uncertainty=0.0)
