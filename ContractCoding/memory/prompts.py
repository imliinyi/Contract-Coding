"""Prompt Engineering memory — role system prompts.

The PromptLibrary is the *single* place where role system prompts live.
Worker passes (PlannerPass / ImplementerPass / LLMReviewer) all pull their
system prompt from here so:

  - prompts can be hot-swapped per team / per project without code changes;
  - prompts are versioned alongside the rest of the registry;
  - integration tests can stub the library to deterministic strings.

The library supports per-(team_id, role) overrides while always falling
back to a built-in default. This mirrors the pull-based principle: passes
ask for `library.get(role, team_id=...)`; nothing is pushed to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from ..core.margin import AgentRole


_DEFAULT_PROMPTS: Dict[AgentRole, str] = {
    AgentRole.PLANNER: (
        "You are a Planner agent for a multi-team coding system. "
        "Decompose the task into 1–5 concrete subtasks. Respond with "
        "JSON having keys subtasks (list of {title, files, boundaries}) "
        "and open_questions (list of str)."
    ),
    AgentRole.IMPLEMENTER: (
        "You are an Implementer agent. Produce concrete code/test "
        "artifacts inside the team workspace. Respond with JSON "
        "having keys artifacts (list of {path, content, intent, "
        "is_test}), decisions (list of {statement, rationale}), "
        "uncertainty (0..1)."
    ),
    AgentRole.REVIEWER: (
        "You are an INDEPENDENT Reviewer. You did not write the code. "
        "Your job is to find real defects, spec drifts, and capsule "
        "contract violations. Only raise concerns for which you can "
        "cite evidence (file path + line span OR failed example). "
        "Respond with JSON: {concerns: [{description, severity, "
        "evidence: [str]}], anti_patterns: [{description, evidence: "
        "[str]}], closed: [concern_id]}. severity ∈ {blocker, major, "
        "minor}."
    ),
    AgentRole.INSPECTOR: (
        "You are an Inspector agent. You PULL the minimum context the "
        "task requires from the registry — never push narrative into "
        "another team. Output JSON describing pulled capsule layers and "
        "flagged risks; never invent capsule references that are not "
        "declared in the task's capsule_dependencies."
    ),
    AgentRole.JUDGE: (
        "You are a Judge agent. Aggregate reviewer concerns + smoke "
        "results + invariants into an approve/reject verdict. Fail "
        "closed when blockers exist, when smoke fails, or when "
        "reviewer-pleasing oscillation is detected."
    ),
    AgentRole.STEWARD: (
        "You are an Interface Steward. Render capsule L3 artifacts "
        "deterministically from the capsule's L1/L2 fields. Do not "
        "write narrative content — emit only stub packages, mock "
        "implementations, smoke tests, and MANIFEST.md."
    ),
    AgentRole.COORDINATOR: (
        "You are a Project Coordinator. You own the frozen plan and "
        "cross-team escalations. Communicate with teams ONLY through "
        "events and the registry — never inject prose into another "
        "team's prompt."
    ),
}


@dataclass
class PromptLibrary:
    """Role → system prompt resolver with per-team overrides."""

    defaults: Dict[AgentRole, str] = field(default_factory=lambda: dict(_DEFAULT_PROMPTS))
    overrides: Dict[Tuple[AgentRole, str], str] = field(default_factory=dict)

    def get(self, role: AgentRole, *, team_id: str = "") -> str:
        if isinstance(role, str):
            role = AgentRole(role)
        if team_id:
            override = self.overrides.get((role, team_id))
            if override:
                return override
        return self.defaults.get(role, "")

    def set_default(self, role: AgentRole, prompt: str) -> None:
        if isinstance(role, str):
            role = AgentRole(role)
        self.defaults[role] = prompt

    def set_override(self, role: AgentRole, team_id: str, prompt: str) -> None:
        if isinstance(role, str):
            role = AgentRole(role)
        if not team_id:
            raise ValueError("team_id required for override")
        self.overrides[(role, team_id)] = prompt

    def clear_override(self, role: AgentRole, team_id: str) -> None:
        if isinstance(role, str):
            role = AgentRole(role)
        self.overrides.pop((role, team_id), None)

    def to_record(self) -> Dict[str, Dict[str, str]]:
        return {
            "defaults": {r.value: p for r, p in self.defaults.items()},
            "overrides": {
                f"{r.value}:{team}": p for (r, team), p in self.overrides.items()
            },
        }


def default_prompt_library() -> PromptLibrary:
    """Module-level factory — every service should hold one shared instance."""
    return PromptLibrary()
