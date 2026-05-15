"""Contract layer — *public commitments* between coordinator + teams.

Core scopes:

  - **ProjectContract**: the global SSOT mirrored from legacy `PlanSpec`.
  - **TeamContract**: each team's work graph, public APIs, dependencies, and
    decisions.
  - **ContractOperation / ContractObligation**: typed exchange records. They
    replace free-form cross-team messages as scheduling inputs.
  - **ContractKernel**: executable projection used by the scheduler.

Private mutable history such as progress, failures, and reviewer memory lives
under `memory/`. LLMs may propose contract operations, but only the
reducer/auditor path accepts them into contract state.
"""

from __future__ import annotations

from .project import BoundedContext, IntentLedger, Invariant, PlanSpec
from .diff import ChangeSet, EMPTY_SHA256, FileChange, sha256_text
from .evidence import ValidationEvidence
from .kernel import ContractKernel, ProjectContract, TeamContract
from .operation import (
    ContractObligation,
    ContractOperation,
    ObligationKind,
    ObligationStatus,
    OperationKind,
    OperationStatus,
)
from .work import (
    ConflictKey,
    TeamScheduleReport,
    TeamWave,
    TeamWorkItem,
    WorkClaim,
    WorkPhase,
    WorkStatus,
)
from .capsule import (
    ALLOWED_TRANSITIONS,
    CapsuleArtifacts,
    CapsuleInterface,
    CapsuleStatus,
    CapsuleTag,
    ExecutableExample,
    InterfaceCapsuleV2,
    L1_MAX_CHARS,
    L2_MAX_CHARS,
    SemVer,
)
from .lifecycle import (
    CapsuleTransitionError,
    TransitionResult,
    advance,
    record_smoke_failure,
    reset_smoke_failures,
)
from .team import Decision, TeamSubContract, WorkingPaper

__all__ = [
    "BoundedContext",
    "ChangeSet",
    "ContractKernel",
    "EMPTY_SHA256",
    "FileChange",
    "ProjectContract",
    "TeamContract",
    "ValidationEvidence",
    "ContractObligation",
    "ContractOperation",
    "ObligationKind",
    "ObligationStatus",
    "OperationKind",
    "OperationStatus",
    "ConflictKey",
    "IntentLedger",
    "Invariant",
    "PlanSpec",
    "TeamScheduleReport",
    "TeamWave",
    "TeamWorkItem",
    "WorkClaim",
    "WorkPhase",
    "WorkStatus",
    "sha256_text",
    "ALLOWED_TRANSITIONS",
    "CapsuleArtifacts",
    "CapsuleInterface",
    "CapsuleStatus",
    "CapsuleTag",
    "ExecutableExample",
    "InterfaceCapsuleV2",
    "L1_MAX_CHARS",
    "L2_MAX_CHARS",
    "SemVer",
    "CapsuleTransitionError",
    "TransitionResult",
    "advance",
    "record_smoke_failure",
    "reset_smoke_failures",
    "Decision",
    "TeamSubContract",
    "WorkingPaper",
]
