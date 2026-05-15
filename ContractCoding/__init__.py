"""ContractCoding: Registry-based long-running multi-agent runtime.

Five-layer architecture (v2.1 restructure):

    core/        — provenance (MarginAnnotation, AgentRole) + cross-team
                   EventLog. The only primitives every other layer may
                   depend on unconditionally.
    contract/    — public commitments: the big ProjectContract (PlanSpec,
                   BoundedContext, Invariant) and per-team TeamSubContract
                   (WorkingPaper, InterfaceCapsuleV2, lifecycle).
    memory/      — private state owned by a team: PromptLibrary (role PEs),
                   SkillLibrary (declarative capability cards), InteractionLog,
                   TaskLedger/FailureLedger, ReviewerMemory.
    agents/      — runtime actors built on top of contract + memory: the
                   Coordinator, Steward, Reviewer, Escalation subsystem.
    worker/      — the 5-pass slice execution pipeline glued to the above.
    registry/    — path-scoped durable store + ACL that mediates every
                   cross-layer write.
    app/         — thin service + CLI façade over the runtime.
    llm/         — concrete backends implementing the worker's LLMPort.

Design rule: contract = public, owner-write, broad-read; memory = private,
team-owned. The split mirrors `Team Topologies` — a team's API is public,
its internal kanban is not.
"""

from __future__ import annotations

# Core primitives
from .core.margin import AgentRole, MarginAnnotation
from .core.events import Event, EventKind, EventLog

# Contract layer (public commitments)
from .contract.project import BoundedContext, IntentLedger, Invariant, PlanSpec
from .contract.capsule import (
    CapsuleArtifacts,
    CapsuleInterface,
    CapsuleStatus,
    CapsuleTag,
    ExecutableExample,
    InterfaceCapsuleV2,
    SemVer,
)
from .contract.lifecycle import (
    CapsuleTransitionError,
    TransitionResult,
    advance,
    record_smoke_failure,
    reset_smoke_failures,
)
from .contract.team import Decision, TeamSubContract, WorkingPaper

# Memory layer (private state)
from .memory.prompts import PromptLibrary, default_prompt_library
from .memory.skills import SkillCard, SkillLibrary, default_skill_library
from .memory.interaction import Interaction, InteractionLog
from .memory.ledgers import (
    FailedHypothesis,
    ProgressEntry,
    TaskItem,
    TaskLedger,
    TaskStatus,
)
from .memory.reviewer_memory import AntiPattern, Concern, ReviewerMemory

# Registry (storage + ACL)
from .registry import (
    Actor,
    ACLDecision,
    RegistryACL,
    RegistryAccessError,
    RegistryBackend,
    RegistryPath,
    RegistryTool,
    default_policy,
)

# Workers + agents
from .worker import (
    ContextPacket,
    ImplementerPass,
    InspectorPass,
    JudgePass,
    LLMPort,
    LLMRequest,
    LLMResult,
    NullLLMPort,
    PipelineConfig,
    PipelineResult,
    PlannerPass,
    SliceArtifact,
    SlicePlan,
    SliceVerdict,
    WorkerPipeline,
)
from .agents.steward import InterfaceSteward, SmokeResult, StewardResult
from .agents.reviewer import LLMReviewer, make_pass as make_reviewer_pass
from .agents.escalation import (
    DeathSpiralDetector,
    Escalation,
    EscalationQueue,
    SpiralVerdict,
)
from .agents.coordinator import (
    CoordinatorTickResult,
    ProjectCoordinator,
    TeamTools,
    make_team_tools,
)

__all__ = [
    "__version__",
    # core
    "AgentRole",
    "MarginAnnotation",
    "Event",
    "EventKind",
    "EventLog",
    # contract — project
    "BoundedContext",
    "IntentLedger",
    "Invariant",
    "PlanSpec",
    # contract — capsule + lifecycle
    "CapsuleArtifacts",
    "CapsuleInterface",
    "CapsuleStatus",
    "CapsuleTag",
    "ExecutableExample",
    "InterfaceCapsuleV2",
    "SemVer",
    "CapsuleTransitionError",
    "TransitionResult",
    "advance",
    "record_smoke_failure",
    "reset_smoke_failures",
    # contract — team
    "Decision",
    "TeamSubContract",
    "WorkingPaper",
    # memory
    "PromptLibrary",
    "default_prompt_library",
    "SkillCard",
    "SkillLibrary",
    "default_skill_library",
    "Interaction",
    "InteractionLog",
    "FailedHypothesis",
    "ProgressEntry",
    "TaskItem",
    "TaskLedger",
    "TaskStatus",
    "AntiPattern",
    "Concern",
    "ReviewerMemory",
    # registry
    "Actor",
    "ACLDecision",
    "RegistryACL",
    "RegistryAccessError",
    "RegistryBackend",
    "RegistryPath",
    "RegistryTool",
    "default_policy",
    # worker
    "ContextPacket",
    "ImplementerPass",
    "InspectorPass",
    "JudgePass",
    "LLMPort",
    "LLMRequest",
    "LLMResult",
    "NullLLMPort",
    "PipelineConfig",
    "PipelineResult",
    "PlannerPass",
    "SliceArtifact",
    "SlicePlan",
    "SliceVerdict",
    "WorkerPipeline",
    # agents
    "InterfaceSteward",
    "SmokeResult",
    "StewardResult",
    "LLMReviewer",
    "make_reviewer_pass",
    "DeathSpiralDetector",
    "Escalation",
    "EscalationQueue",
    "SpiralVerdict",
    "CoordinatorTickResult",
    "ProjectCoordinator",
    "TeamTools",
    "make_team_tools",
]

__version__ = "2.1.0"
