"""Memory layer — *private team state*.

This package contains everything that is owned by a single team and is NOT
part of its public contract:

  - **PromptLibrary** (`prompts.py`): role system prompts. Hot-swappable;
    nothing else in the codebase should hardcode prompts.
  - **SkillCard / SkillLibrary** (`skills.py`): declarative capability
    cards (akin to the legacy SKILL.md assets). Inspector pulls relevant
    cards into a packet; cards never push themselves.
  - **InteractionLog** (`interaction.py`): natural-language recall only. It is
    distinct from typed `ContractOperation`s and does not drive scheduling.
  - **TaskLedger / FailureLedger / ProgressEntry** (`ledgers.py`): the
    Magentic-One inspired mutable runtime state.
  - **ReviewerMemory / Concern / AntiPattern** (`reviewer_memory.py`): the
    persistent reviewer state across slices.

Path-scoped ACL guarantees: `/ledgers/<team>/**` is team-private with
coordinator-read; `/memory/<team>/**` follows the same rule.
"""

from __future__ import annotations

from .prompts import PromptLibrary, default_prompt_library
from .skills import SkillCard, SkillLibrary, default_skill_library
from .interaction import Interaction, InteractionLog
from .ledgers import (
    FailedHypothesis,
    ProgressEntry,
    TaskItem,
    TaskLedger,
    TaskStatus,
)
from .reviewer_memory import AntiPattern, Concern, ReviewerMemory

__all__ = [
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
]
