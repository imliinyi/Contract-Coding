"""Reviewer memory — persistent across slices.

Closes the gap of "reviewer rediscovers same flaws every slice" (P6
persistent memory). Tracks:
  - Open / closed `Concern`s (with reopen counts → reviewer-pleasing
    detection signal).
  - Seen `AntiPattern`s with seen counts and example evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional


@dataclass
class Concern:
    concern_id: str
    description: str
    closed: bool = False
    open_count: int = 0
    last_evidence: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_record(self) -> Dict[str, Any]:
        return {
            "concern_id": self.concern_id,
            "description": self.description,
            "closed": bool(self.closed),
            "open_count": int(self.open_count),
            "last_evidence": list(self.last_evidence),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "Concern":
        payload = dict(payload or {})
        return cls(
            concern_id=str(payload.get("concern_id", "")),
            description=str(payload.get("description", "")),
            closed=bool(payload.get("closed", False)),
            open_count=int(payload.get("open_count", 0) or 0),
            last_evidence=[str(v) for v in payload.get("last_evidence", []) or []],
            created_at=float(payload.get("created_at", time.time())),
        )


@dataclass
class AntiPattern:
    pattern_id: str
    description: str
    seen_count: int = 1
    example_evidence: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "seen_count": int(self.seen_count),
            "example_evidence": list(self.example_evidence),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "AntiPattern":
        payload = dict(payload or {})
        return cls(
            pattern_id=str(payload.get("pattern_id", "")),
            description=str(payload.get("description", "")),
            seen_count=int(payload.get("seen_count", 1) or 1),
            example_evidence=[str(v) for v in payload.get("example_evidence", []) or []],
        )


@dataclass
class ReviewerMemory:
    team_id: str
    seen_anti_patterns: List[AntiPattern] = field(default_factory=list)
    open_concerns: List[Concern] = field(default_factory=list)
    closed_concerns: List[Concern] = field(default_factory=list)

    def upsert_anti_pattern(self, pattern: AntiPattern) -> None:
        for existing in self.seen_anti_patterns:
            if existing.pattern_id == pattern.pattern_id:
                existing.seen_count += 1
                existing.example_evidence.extend(pattern.example_evidence)
                return
        self.seen_anti_patterns.append(pattern)

    def reopen(self, concern_id: str, evidence: List[str]) -> Optional[Concern]:
        for concern in self.closed_concerns:
            if concern.concern_id == concern_id:
                concern.closed = False
                concern.open_count += 1
                concern.last_evidence = list(evidence)
                self.open_concerns.append(concern)
                self.closed_concerns.remove(concern)
                return concern
        return None

    def close(self, concern_id: str) -> None:
        for concern in list(self.open_concerns):
            if concern.concern_id == concern_id:
                concern.closed = True
                self.open_concerns.remove(concern)
                self.closed_concerns.append(concern)
                return

    def reviewer_pleasing_signal(self, max_open_close_cycles: int = 2) -> List[str]:
        return [
            c.concern_id
            for c in self.open_concerns + self.closed_concerns
            if c.open_count > max_open_close_cycles
        ]

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "seen_anti_patterns": [p.to_record() for p in self.seen_anti_patterns],
            "open_concerns": [c.to_record() for c in self.open_concerns],
            "closed_concerns": [c.to_record() for c in self.closed_concerns],
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ReviewerMemory":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            seen_anti_patterns=[
                AntiPattern.from_mapping(v) for v in payload.get("seen_anti_patterns", []) or []
            ],
            open_concerns=[Concern.from_mapping(v) for v in payload.get("open_concerns", []) or []],
            closed_concerns=[
                Concern.from_mapping(v) for v in payload.get("closed_concerns", []) or []
            ],
        )
