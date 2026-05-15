"""Project contract — the frozen post-onboarding specification.

Design constraints enforced here:
  - **C4 Progressive Commitment**: plan captures bounded_contexts + team
    boundaries + capability NAMES — but never API signatures. Signatures
    emerge during team execution as DRAFT capsules.
  - **C1 No Narration**: `allowed_consumers` is the only way another team is
    permitted to depend on this one. Prompt-level references are invalid.

Inspired by DDD bounded contexts + Team Topologies "team API".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class Invariant:
    """Cross-team hard rule that no team may violate."""

    id: str
    description: str
    scope: str = "global"
    severity: str = "hard"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "scope": self.scope,
            "severity": self.severity,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "Invariant":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")),
            description=str(payload.get("description", "")),
            scope=str(payload.get("scope", "global")),
            severity=str(payload.get("severity", "hard")),
        )


@dataclass
class IntentLedger:
    """Human-confirmed onboarding artefact. Mutable before plan freeze."""

    goal: str
    acceptance_signals: List[str] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    user_edits: List[Dict[str, Any]] = field(default_factory=list)
    frozen: bool = False

    def to_record(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "acceptance_signals": list(self.acceptance_signals),
            "non_goals": list(self.non_goals),
            "assumptions": list(self.assumptions),
            "user_edits": list(self.user_edits),
            "frozen": bool(self.frozen),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "IntentLedger":
        payload = dict(payload or {})
        return cls(
            goal=str(payload.get("goal", "")),
            acceptance_signals=[str(v) for v in payload.get("acceptance_signals", []) or []],
            non_goals=[str(v) for v in payload.get("non_goals", []) or []],
            assumptions=[str(v) for v in payload.get("assumptions", []) or []],
            user_edits=list(payload.get("user_edits", []) or []),
            frozen=bool(payload.get("frozen", False)),
        )


@dataclass(frozen=True)
class BoundedContext:
    """A single team's boundary: WHAT it owns, NOT how it exposes it."""

    team_id: str
    purpose_one_liner: str
    capability_names: List[str]
    allowed_consumers: List[str]
    workspace_path: str

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "purpose_one_liner": self.purpose_one_liner,
            "capability_names": list(self.capability_names),
            "allowed_consumers": list(self.allowed_consumers),
            "workspace_path": self.workspace_path,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "BoundedContext":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            purpose_one_liner=str(payload.get("purpose_one_liner", ""))[:120],
            capability_names=[str(v) for v in payload.get("capability_names", []) or []],
            allowed_consumers=[str(v) for v in payload.get("allowed_consumers", []) or []],
            workspace_path=str(payload.get("workspace_path", "")),
        )


@dataclass
class PlanSpec:
    """Frozen after Coordinator Onboarding → Shaping → Scoping → Alignment."""

    intent: IntentLedger
    bounded_contexts: List[BoundedContext] = field(default_factory=list)
    cross_team_invariants: List[Invariant] = field(default_factory=list)
    plan_version: str = "v1"
    frozen: bool = False

    def team_ids(self) -> List[str]:
        return [ctx.team_id for ctx in self.bounded_contexts]

    def context_of(self, team_id: str) -> BoundedContext:
        for ctx in self.bounded_contexts:
            if ctx.team_id == team_id:
                return ctx
        raise KeyError(f"no bounded_context for team_id={team_id!r}")

    def validate(self) -> List[str]:
        errors: List[str] = []
        ids = [ctx.team_id for ctx in self.bounded_contexts]
        if len(ids) != len(set(ids)):
            errors.append("duplicate team_id in bounded_contexts")
        for ctx in self.bounded_contexts:
            if len(ctx.purpose_one_liner) > 120:
                errors.append(f"{ctx.team_id}: purpose_one_liner exceeds 120 chars")
            if not ctx.capability_names:
                errors.append(f"{ctx.team_id}: no capability_names declared")
            unknown = [c for c in ctx.allowed_consumers if c not in ids and c != "*"]
            if unknown:
                errors.append(f"{ctx.team_id}: allowed_consumers references unknown teams: {unknown}")
        if len(self.bounded_contexts) > 8:
            errors.append(
                "more than 8 bounded_contexts; consider merging (Amazon two-pizza rule)"
            )
        return errors

    def to_record(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.to_record(),
            "bounded_contexts": [ctx.to_record() for ctx in self.bounded_contexts],
            "cross_team_invariants": [inv.to_record() for inv in self.cross_team_invariants],
            "plan_version": self.plan_version,
            "frozen": bool(self.frozen),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "PlanSpec":
        payload = dict(payload or {})
        return cls(
            intent=IntentLedger.from_mapping(payload.get("intent", {}) or {}),
            bounded_contexts=[
                BoundedContext.from_mapping(v) for v in payload.get("bounded_contexts", []) or []
            ],
            cross_team_invariants=[
                Invariant.from_mapping(v) for v in payload.get("cross_team_invariants", []) or []
            ],
            plan_version=str(payload.get("plan_version", "v1")),
            frozen=bool(payload.get("frozen", False)),
        )
