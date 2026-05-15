"""Agents layer — runtime actors built on top of contract + memory.

Each agent is a thin object that uses a `RegistryTool` to interact with the
contract registry, pulls prompts from `memory.PromptLibrary`, and pulls
skill cards from `memory.SkillLibrary`. Agents never write into another
team's prompt; they emit events instead (C1 No Narration).
"""

from __future__ import annotations

from .coordinator import (
    CoordinatorTickResult,
    ProjectCoordinator,
    TeamTools,
    make_team_tools,
)
from .steward import InterfaceSteward, SmokeResult, StewardResult
from .reviewer import LLMReviewer, make_pass as make_reviewer_pass
from .escalation import (
    DeathSpiralDetector,
    Escalation,
    EscalationQueue,
    SpiralVerdict,
)
from .auditor import ContractAuditor
from .reducer import ContractReducer, ReducerResult
from .scheduler import SchedulerConfig, TeamScheduler

__all__ = [
    "CoordinatorTickResult",
    "ProjectCoordinator",
    "TeamTools",
    "make_team_tools",
    "InterfaceSteward",
    "SmokeResult",
    "StewardResult",
    "LLMReviewer",
    "make_reviewer_pass",
    "DeathSpiralDetector",
    "Escalation",
    "EscalationQueue",
    "SpiralVerdict",
    "ContractAuditor",
    "ContractReducer",
    "ReducerResult",
    "SchedulerConfig",
    "TeamScheduler",
]
