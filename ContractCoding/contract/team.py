"""Team contract — the per-team public commitment.

Owns the WorkingPaper (purpose, owned invariants, decisions) plus the
TeamSubContract aggregate. The aggregate is the only object that crosses the
contract/memory boundary: it holds a reference to the team's *private*
ledgers + reviewer memory so callers (Coordinator, Steward, Reviewer) can
get a complete snapshot in one read.

WorkingPaper is the Co-Mathematician "living working paper". Decisions are
margin-stamped and cannot be silently rewritten — supersession is via
`superseded_by` link.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Any, Dict, List, Optional

from ..core.margin import MarginAnnotation
from ..memory.ledgers import FailedHypothesis, TaskLedger
from ..memory.reviewer_memory import ReviewerMemory


@dataclass
class Decision:
    decision_id: str
    statement: str
    rationale: str = ""
    margin: MarginAnnotation = field(default_factory=MarginAnnotation.system)
    superseded_by: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "statement": self.statement,
            "rationale": self.rationale,
            "margin": self.margin.to_record(),
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "Decision":
        payload = dict(payload or {})
        return cls(
            decision_id=str(payload.get("decision_id", "") or f"dec:{uuid.uuid4().hex[:8]}"),
            statement=str(payload.get("statement", "")),
            rationale=str(payload.get("rationale", "")),
            margin=MarginAnnotation.from_mapping(payload.get("margin", {}) or {}),
            superseded_by=payload.get("superseded_by"),
        )


@dataclass
class WorkingPaper:
    team_id: str
    bounded_context_purpose: str = ""
    owned_invariants: List[str] = field(default_factory=list)
    owned_canonical_types: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    decisions: List[Decision] = field(default_factory=list)
    local_conventions: Dict[str, Any] = field(default_factory=dict)

    def add_decision(self, statement: str, rationale: str, margin: MarginAnnotation) -> Decision:
        decision = Decision(
            decision_id=f"dec:{uuid.uuid4().hex[:8]}",
            statement=statement,
            rationale=rationale,
            margin=margin,
        )
        self.decisions.append(decision)
        return decision

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "bounded_context_purpose": self.bounded_context_purpose,
            "owned_invariants": list(self.owned_invariants),
            "owned_canonical_types": list(self.owned_canonical_types),
            "open_questions": list(self.open_questions),
            "decisions": [d.to_record() for d in self.decisions],
            "local_conventions": dict(self.local_conventions),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "WorkingPaper":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            bounded_context_purpose=str(payload.get("bounded_context_purpose", "")),
            owned_invariants=[str(v) for v in payload.get("owned_invariants", []) or []],
            owned_canonical_types=[str(v) for v in payload.get("owned_canonical_types", []) or []],
            open_questions=[str(v) for v in payload.get("open_questions", []) or []],
            decisions=[Decision.from_mapping(v) for v in payload.get("decisions", []) or []],
            local_conventions=dict(payload.get("local_conventions", {}) or {}),
        )


@dataclass
class TeamSubContract:
    """Aggregate snapshot of a team's contract + memory.

    Loaded by Coordinator/Steward/Reviewer for a holistic view. The fields
    span both layers intentionally so the snapshot is the single object
    passed into the worker pipeline via `ContextPacket.subcontract`.
    """

    team_id: str
    working_paper: WorkingPaper
    task_ledger: TaskLedger
    failure_ledger: List[FailedHypothesis] = field(default_factory=list)
    reviewer_memory: ReviewerMemory = field(default_factory=lambda: ReviewerMemory(team_id=""))

    @classmethod
    def empty(cls, team_id: str) -> "TeamSubContract":
        return cls(
            team_id=team_id,
            working_paper=WorkingPaper(team_id=team_id),
            task_ledger=TaskLedger(team_id=team_id),
            failure_ledger=[],
            reviewer_memory=ReviewerMemory(team_id=team_id),
        )

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "working_paper": self.working_paper.to_record(),
            "task_ledger": self.task_ledger.to_record(),
            "failure_ledger": [f.to_record() for f in self.failure_ledger],
            "reviewer_memory": self.reviewer_memory.to_record(),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamSubContract":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            working_paper=WorkingPaper.from_mapping(payload.get("working_paper", {}) or {}),
            task_ledger=TaskLedger.from_mapping(payload.get("task_ledger", {}) or {}),
            failure_ledger=[
                FailedHypothesis.from_mapping(v) for v in payload.get("failure_ledger", []) or []
            ],
            reviewer_memory=ReviewerMemory.from_mapping(
                payload.get("reviewer_memory", {}) or {}
            ),
        )
