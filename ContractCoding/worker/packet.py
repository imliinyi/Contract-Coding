"""Pipeline-shared dataclasses (no LLM dependency).

Imports are layered:
  - contract.* for capsule/plan/team aggregates (public commitments);
  - memory.* for ledgers + reviewer memory (private state).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..contract.capsule import InterfaceCapsuleV2
from ..contract.diff import ChangeSet
from ..contract.project import BoundedContext, PlanSpec
from ..contract.team import TeamSubContract, WorkingPaper
from ..contract.work import TeamWorkItem
from ..memory.ledgers import FailedHypothesis, TaskItem
from ..memory.reviewer_memory import ReviewerMemory


@dataclass
class SlicePlan:
    """Output of the Planner pass — a structured to-do list."""

    subtasks: List[Dict[str, Any]] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    forbidden_patterns: List[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class SliceArtifact:
    """One concrete file or change produced by the Implementer."""

    path: str          # workspace-relative, e.g. "src/auth.py"
    content: str
    intent: str = ""   # short summary
    is_test: bool = False


@dataclass
class SliceVerdict:
    approved: bool
    reasons: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    reviewer_concerns: List[str] = field(default_factory=list)
    smoke_passed: Optional[bool] = None
    fingerprint: Optional[str] = None


@dataclass
class ContextPacket:
    """Cross-pass state carried for one TaskItem.

    Inspector populates `consumed_capsules`, `working_paper`, `prior_failures`,
    and role-scoped `skill_fragments_by_role` from `memory.SkillLibrary`.
    Implementer reads these and writes `artifacts`. Reviewer reads everything
    and writes `reviewer_concerns`. Judge reads the entire packet and writes
    `verdict`.
    """

    plan: PlanSpec
    bounded_context: BoundedContext
    subcontract: TeamSubContract
    task: Any
    work_item: Optional[TeamWorkItem] = None
    consumed_capsules: List[InterfaceCapsuleV2] = field(default_factory=list)
    consumed_capsule_layers: Dict[str, str] = field(default_factory=dict)  # capsule_id -> "L2"/"L3"
    prior_failures: List[FailedHypothesis] = field(default_factory=list)
    skill_fragments_by_role: Dict[str, List[str]] = field(default_factory=dict)
    slice_plan: Optional[SlicePlan] = None
    change_set: Optional[ChangeSet] = None
    artifacts: List[SliceArtifact] = field(default_factory=list)
    reviewer_concerns: List[str] = field(default_factory=list)
    reviewer_memory: Optional[ReviewerMemory] = None
    verdict: Optional[SliceVerdict] = None
    blockers: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
