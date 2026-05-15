"""Registry ACL — path-scoped writer policy.

Implements design constraint: each logical area of the registry has exactly
one writer role; readers are broad. Producers cannot accidentally clobber
another team's working state.

Default policy (matches Part 2 §3.2 of the design doc):

    /plan/**                  : COORDINATOR (write), ALL (read)
    /capsules/<team>/**       : STEWARD of <team> (write), ALL (read)
    /workspace/<team>/**      : IMPLEMENTER of <team> (write), <team> (read)
    /ledgers/<team>/**        : ANY role of <team> (write), <team> (read);
                                COORDINATOR (read)
    /events.log               : ANY (append-only); ALL (read)
    /escalations/**           : COORDINATOR + originating team (write/read)

The ACL is a pure function: it does not mutate state. The RegistryTool
calls into it before forwarding to the backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from ..core.margin import AgentRole
from .backend import RegistryPath


class RegistryAccessError(PermissionError):
    """Raised when a registry write/read is denied by ACL."""


class Op(str, Enum):
    READ = "read"
    WRITE = "write"
    APPEND = "append"
    LIST = "list"


@dataclass(frozen=True)
class ACLDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class Actor:
    agent_id: str
    role: AgentRole
    team_id: str = ""

    @classmethod
    def coordinator(cls, agent_id: str = "coordinator") -> "Actor":
        return cls(agent_id=agent_id, role=AgentRole.COORDINATOR, team_id="")

    @classmethod
    def steward_of(cls, team_id: str, agent_id: str = "") -> "Actor":
        return cls(agent_id=agent_id or f"steward:{team_id}", role=AgentRole.STEWARD, team_id=team_id)


PolicyFn = Callable[[Actor, Op, RegistryPath], ACLDecision]


# ---------------------------------------------------------------------------
# Default policy
# ---------------------------------------------------------------------------


def _segments(path: RegistryPath) -> List[str]:
    return path.parts()


def _is_under(path: RegistryPath, prefix_parts: List[str]) -> bool:
    parts = _segments(path)
    if len(parts) < len(prefix_parts):
        return False
    return parts[: len(prefix_parts)] == prefix_parts


def default_policy(actor: Actor, op: Op, path: RegistryPath) -> ACLDecision:
    parts = _segments(path)
    if not parts:
        return ACLDecision(False, "empty path")

    head = parts[0]

    # /plan/**
    if head == "plan" or path.normalised() == "/plan.json":
        if op == Op.READ or op == Op.LIST:
            return ACLDecision(True)
        if actor.role == AgentRole.COORDINATOR:
            return ACLDecision(True)
        return ACLDecision(False, "plan area is coordinator-write-only")

    # /events.log
    if head == "events.log":
        if op == Op.READ or op == Op.APPEND:
            return ACLDecision(True)
        return ACLDecision(False, "events.log is append+read only")

    # /contract/**
    if head == "contract":
        if op in (Op.READ, Op.LIST):
            return ACLDecision(True)
        if len(parts) >= 2 and parts[1] == "operations.jsonl" and op == Op.APPEND:
            return ACLDecision(True)
        if actor.role == AgentRole.COORDINATOR:
            return ACLDecision(True, "coordinator owns contract reductions")
        return ACLDecision(False, "contract state is reducer/coordinator-write-only")

    # /capsules/<team>/**
    if head == "capsules":
        if op == Op.READ or op == Op.LIST:
            return ACLDecision(True)
        if len(parts) < 2:
            return ACLDecision(False, "capsule write requires team scope")
        owning_team = parts[1]
        if actor.role == AgentRole.STEWARD and actor.team_id == owning_team:
            return ACLDecision(True)
        if actor.role == AgentRole.COORDINATOR:
            return ACLDecision(True, "coordinator override")
        return ACLDecision(False, f"capsules/{owning_team} writable only by its steward")

    # /workspace/<team>/**
    if head == "workspace":
        if len(parts) < 2:
            return ACLDecision(False, "workspace requires team scope")
        owning_team = parts[1]
        if actor.team_id == owning_team and actor.role in (
            AgentRole.IMPLEMENTER,
            AgentRole.PLANNER,
            AgentRole.INSPECTOR,
            AgentRole.REVIEWER,
            AgentRole.JUDGE,
            AgentRole.STEWARD,
        ):
            return ACLDecision(True)
        if actor.role == AgentRole.COORDINATOR and op in (Op.READ, Op.LIST):
            return ACLDecision(True)
        if op in (Op.READ, Op.LIST) and actor.team_id == owning_team:
            return ACLDecision(True)
        return ACLDecision(False, f"workspace/{owning_team} owned by that team")

    # /ledgers/<team>/**
    if head == "ledgers":
        if len(parts) < 2:
            return ACLDecision(False, "ledgers requires team scope")
        owning_team = parts[1]
        if actor.team_id == owning_team:
            return ACLDecision(True)
        if op in (Op.READ, Op.LIST) and actor.role == AgentRole.COORDINATOR:
            return ACLDecision(True)
        return ACLDecision(False, f"ledgers/{owning_team} private to its team")

    # /escalations/**
    if head == "escalations":
        if actor.role == AgentRole.COORDINATOR:
            return ACLDecision(True)
        if op in (Op.READ, Op.WRITE, Op.APPEND, Op.LIST):
            # any team may file an escalation; reads broad to preserve transparency
            return ACLDecision(True)
        return ACLDecision(False, "escalations restricted")

    return ACLDecision(False, f"unknown registry area: /{head}")


class RegistryACL:
    """Wraps a policy function so we can decorate / swap in tests."""

    def __init__(self, policy: Optional[PolicyFn] = None):
        self.policy = policy or default_policy

    def check(self, actor: Actor, op: Op, path: RegistryPath) -> ACLDecision:
        return self.policy(actor, op, path)

    def enforce(self, actor: Actor, op: Op, path: RegistryPath) -> None:
        decision = self.check(actor, op, path)
        if not decision.allowed:
            raise RegistryAccessError(
                f"actor={actor.agent_id} role={actor.role.value} team={actor.team_id} "
                f"op={op.value} path={path.normalised()} denied: {decision.reason}"
            )
