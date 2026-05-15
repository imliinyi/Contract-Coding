"""Contract SSOT and executable scheduling kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .capsule import InterfaceCapsuleV2
from .operation import ContractObligation
from .project import BoundedContext, IntentLedger, Invariant, PlanSpec
from .team import Decision, WorkingPaper
from .work import TeamWorkItem, WorkStatus


@dataclass
class ProjectContract:
    """Global single source of truth for public architecture."""

    intent: IntentLedger
    bounded_contexts: List[BoundedContext] = field(default_factory=list)
    cross_team_invariants: List[Invariant] = field(default_factory=list)
    module_graph: Dict[str, List[str]] = field(default_factory=dict)
    public_dependencies: Dict[str, List[str]] = field(default_factory=dict)
    contract_version: str = "v1"
    frozen: bool = False

    @classmethod
    def from_plan(cls, plan: PlanSpec) -> "ProjectContract":
        return cls(
            intent=plan.intent,
            bounded_contexts=list(plan.bounded_contexts),
            cross_team_invariants=list(plan.cross_team_invariants),
            contract_version=plan.plan_version,
            frozen=plan.frozen,
        )

    def to_plan(self) -> PlanSpec:
        return PlanSpec(
            intent=self.intent,
            bounded_contexts=list(self.bounded_contexts),
            cross_team_invariants=list(self.cross_team_invariants),
            plan_version=self.contract_version,
            frozen=self.frozen,
        )

    def team_ids(self) -> List[str]:
        return [ctx.team_id for ctx in self.bounded_contexts]

    def context_of(self, team_id: str) -> BoundedContext:
        for ctx in self.bounded_contexts:
            if ctx.team_id == team_id:
                return ctx
        raise KeyError(f"no bounded_context for team_id={team_id!r}")

    def validate(self) -> List[str]:
        return self.to_plan().validate()

    def to_record(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.to_record(),
            "bounded_contexts": [ctx.to_record() for ctx in self.bounded_contexts],
            "cross_team_invariants": [inv.to_record() for inv in self.cross_team_invariants],
            "module_graph": {k: list(v) for k, v in self.module_graph.items()},
            "public_dependencies": {k: list(v) for k, v in self.public_dependencies.items()},
            "contract_version": self.contract_version,
            "frozen": bool(self.frozen),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ProjectContract":
        payload = dict(payload or {})
        return cls(
            intent=IntentLedger.from_mapping(payload.get("intent", {}) or {}),
            bounded_contexts=[
                BoundedContext.from_mapping(v) for v in payload.get("bounded_contexts", []) or []
            ],
            cross_team_invariants=[
                Invariant.from_mapping(v) for v in payload.get("cross_team_invariants", []) or []
            ],
            module_graph={
                str(k): [str(vv) for vv in (v or [])]
                for k, v in (payload.get("module_graph", {}) or {}).items()
            },
            public_dependencies={
                str(k): [str(vv) for vv in (v or [])]
                for k, v in (payload.get("public_dependencies", {}) or {}).items()
            },
            contract_version=str(payload.get("contract_version", payload.get("plan_version", "v1"))),
            frozen=bool(payload.get("frozen", False)),
        )


@dataclass
class TeamContract:
    """Per-team contract state used by scheduler and reducer."""

    team_id: str
    working_paper: WorkingPaper = field(default_factory=lambda: WorkingPaper(team_id=""))
    work_items: List[TeamWorkItem] = field(default_factory=list)
    public_apis: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    decisions: List[Decision] = field(default_factory=list)
    open_obligations: List[str] = field(default_factory=list)

    @classmethod
    def empty(cls, team_id: str) -> "TeamContract":
        return cls(team_id=team_id, working_paper=WorkingPaper(team_id=team_id))

    @classmethod
    def from_subcontract(cls, subcontract: Any) -> "TeamContract":
        paper = getattr(subcontract, "working_paper", WorkingPaper(team_id=getattr(subcontract, "team_id", "")))
        team_id = str(getattr(subcontract, "team_id", paper.team_id))
        ledger = getattr(subcontract, "task_ledger", None)
        items = [
            TeamWorkItem.from_task_item(team_id, task)
            for task in (getattr(ledger, "items", []) or [])
        ]
        return cls(
            team_id=team_id,
            working_paper=paper,
            work_items=items,
            decisions=list(getattr(paper, "decisions", []) or []),
        )

    def by_work_id(self, work_id: str) -> Optional[TeamWorkItem]:
        return next((item for item in self.work_items if item.work_id == work_id), None)

    def by_task_id(self, task_id: str) -> Optional[TeamWorkItem]:
        return next((item for item in self.work_items if item.task_id == task_id), None)

    def upsert_work_item(self, item: TeamWorkItem) -> TeamWorkItem:
        existing = self.by_work_id(item.work_id) or self.by_task_id(item.task_id)
        if existing is None:
            self.work_items.append(item)
            return item
        idx = self.work_items.index(existing)
        self.work_items[idx] = item
        return item

    def pending_items(self) -> List[TeamWorkItem]:
        return [
            item
            for item in self.work_items
            if (item.status.value if hasattr(item.status, "value") else str(item.status))
            in (WorkStatus.PENDING.value, WorkStatus.ACTIVE.value)
        ]

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "working_paper": self.working_paper.to_record(),
            "work_items": [item.to_record() for item in self.work_items],
            "public_apis": dict(self.public_apis),
            "dependencies": {k: list(v) for k, v in self.dependencies.items()},
            "decisions": [d.to_record() for d in self.decisions],
            "open_obligations": list(self.open_obligations),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamContract":
        payload = dict(payload or {})
        team_id = str(payload.get("team_id", ""))
        return cls(
            team_id=team_id,
            working_paper=WorkingPaper.from_mapping(
                payload.get("working_paper", {}) or {"team_id": team_id}
            ),
            work_items=[
                TeamWorkItem.from_mapping(v) for v in payload.get("work_items", []) or []
            ],
            public_apis=dict(payload.get("public_apis", {}) or {}),
            dependencies={
                str(k): [str(vv) for vv in (v or [])]
                for k, v in (payload.get("dependencies", {}) or {}).items()
            },
            decisions=[Decision.from_mapping(v) for v in payload.get("decisions", []) or []],
            open_obligations=[str(v) for v in payload.get("open_obligations", []) or []],
        )


@dataclass
class ContractKernel:
    """Executable view consumed by the scheduler."""

    project: ProjectContract
    teams: Dict[str, TeamContract] = field(default_factory=dict)
    capsules: Dict[str, InterfaceCapsuleV2] = field(default_factory=dict)
    obligations: List[ContractObligation] = field(default_factory=list)

    def all_work_items(self) -> List[TeamWorkItem]:
        items: List[TeamWorkItem] = []
        for team in self.teams.values():
            items.extend(team.work_items)
        return items

    def work_by_id(self) -> Dict[str, TeamWorkItem]:
        out: Dict[str, TeamWorkItem] = {}
        for item in self.all_work_items():
            out[item.work_id] = item
            out[item.task_id] = item
        return out
