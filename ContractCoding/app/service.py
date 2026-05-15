"""Application service over the registry-based runtime.

Wraps `ProjectCoordinator` + per-team `WorkerPipeline` construction so the
CLI can stay thin. Persistence lives in the registry filesystem layout
documented in `registry/backend.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..agents.coordinator import (
    CoordinatorTickResult,
    ProjectCoordinator,
    make_team_tools,
)
from ..agents.reviewer import make_pass as make_reviewer_pass
from ..config import Config
from ..contract.project import BoundedContext, Invariant, PlanSpec
from ..memory.ledgers import TaskItem
from ..memory.prompts import PromptLibrary, default_prompt_library
from ..memory.skills import SkillLibrary, default_skill_library
from ..registry import RegistryACL, RegistryBackend
from ..worker import (
    NullLLMPort,
    PipelineConfig,
    WorkerPipeline,
)
from ..worker.protocol import LLMPort


@dataclass
class TickReport:
    ran_tasks: int
    approved: int
    rejected: int
    notes: List[str]
    spiral_team_ids: List[str]
    schedule_id: str = ""
    waves: int = 0
    blocked: int = 0


class ContractCodingService:
    """Thin façade used by the CLI."""

    def __init__(self, config: Config):
        self.config = config
        self.backend = RegistryBackend(root=config.WORKSPACE_DIR)
        self.acl = RegistryACL()
        self.coordinator = ProjectCoordinator(self.backend, self.acl)
        self.coordinator.max_item_repair_attempts = config.AUTO_ITEM_REPAIR_MAX
        self.coordinator.scheduler.config.max_parallel_teams = config.MAX_PARALLEL_TEAMS
        self.coordinator.scheduler.config.max_parallel_items_per_team = (
            config.MAX_PARALLEL_ITEMS_PER_TEAM
        )
        self.prompts: PromptLibrary = default_prompt_library()
        self.skills: SkillLibrary = default_skill_library()

    # -------------------------------------------------------------- lifecycle

    def onboard(
        self,
        *,
        goal: str,
        bounded_contexts: List[BoundedContext],
        invariants: Optional[List[Invariant]] = None,
        acceptance_signals: Optional[List[str]] = None,
        non_goals: Optional[List[str]] = None,
        assumptions: Optional[List[str]] = None,
        plan_version: str = "v1",
        freeze: bool = True,
    ) -> PlanSpec:
        return self.coordinator.onboard(
            goal=goal,
            bounded_contexts=bounded_contexts,
            invariants=invariants,
            acceptance_signals=acceptance_signals,
            non_goals=non_goals,
            assumptions=assumptions,
            plan_version=plan_version,
            freeze=freeze,
        )

    def activate_team(
        self,
        team_id: str,
        *,
        initial_tasks: Optional[List[TaskItem]] = None,
    ) -> None:
        self.coordinator.activate_team(team_id, initial_tasks=initial_tasks)

    # -------------------------------------------------------------- pipelines

    def _build_llm(self, *, offline: bool) -> LLMPort:
        if offline or self.config.OFFLINE_LLM or not self.config.OPENAI_API_KEY:
            return NullLLMPort()
        from ..llm import OpenAILLMPort
        return OpenAILLMPort(self.config)

    def _build_reviewer_llm(self, *, offline: bool) -> LLMPort:
        # Reviewer must be a SEPARATE port instance from the implementer.
        return self._build_llm(offline=offline)

    def build_pipelines(self, *, offline: bool = False) -> Dict[str, WorkerPipeline]:
        plan = self.coordinator.tool.get_plan()
        if plan is None:
            raise RuntimeError("plan not yet onboarded")
        impl_llm = self._build_llm(offline=offline)
        review_llm = self._build_reviewer_llm(offline=offline)
        pipelines: Dict[str, WorkerPipeline] = {}
        for ctx in plan.bounded_contexts:
            tools = make_team_tools(self.backend, self.acl, ctx.team_id)
            reviewer = make_reviewer_pass(tools.reviewer, review_llm, prompts=self.prompts)
            pipeline = WorkerPipeline(
                tool=tools.implementer,
                llm=impl_llm,
                reviewer=reviewer,
                control_tool=tools.planner,
                planner_tool=tools.planner,
                inspector_tool=tools.inspector,
                implementer_tool=tools.implementer,
                judge_tool=tools.judge,
                config=PipelineConfig(
                    planner_max_tokens=min(2048, self.config.OPENAI_API_MAX_TOKENS),
                    implementer_max_tokens=self.config.OPENAI_API_MAX_TOKENS,
                ),
                prompts=self.prompts,
                skills=self.skills,
            )
            pipelines[ctx.team_id] = pipeline
        return pipelines

    # -------------------------------------------------------------- ticks

    def tick(
        self,
        *,
        offline: bool = False,
        max_per_team: int = 1,
    ) -> TickReport:
        pipelines = self.build_pipelines(offline=offline)
        result = self.coordinator.run_once(
            pipelines=pipelines,
            max_per_team=max_per_team,
        )
        return self._to_report(result)

    def orchestrate(
        self,
        *,
        offline: bool = False,
        max_ticks: Optional[int] = None,
        max_per_team: int = 1,
    ) -> List[TickReport]:
        pipelines = self.build_pipelines(offline=offline)
        n = max_ticks if max_ticks is not None else self.config.MAX_TICKS
        reports: List[TickReport] = []
        for _ in range(n):
            result = self.coordinator.run_once(
                pipelines=pipelines,
                max_per_team=max_per_team,
            )
            reports.append(self._to_report(result))
            if result.ran_tasks == 0:
                break
        return reports

    # -------------------------------------------------------------- queries

    def status(self) -> Dict[str, Any]:
        plan = self.coordinator.tool.get_plan()
        if plan is None:
            return {"plan": None, "teams": []}
        teams = []
        for ctx in plan.bounded_contexts:
            ledger = self.coordinator.tool.get_task_ledger(ctx.team_id)
            n_total = len(ledger.items) if ledger else 0
            n_done = sum(1 for it in (ledger.items if ledger else []) if it.status.value == "done")
            obligations = [
                o for o in self.coordinator.tool.read_obligations()
                if o.team_id == ctx.team_id and o.status.value == "open"
            ]
            teams.append({
                "team_id": ctx.team_id,
                "purpose": ctx.purpose_one_liner,
                "tasks_total": n_total,
                "tasks_done": n_done,
                "open_obligations": len(obligations),
            })
        return {
            "plan": {
                "goal": plan.intent.goal,
                "frozen": plan.frozen,
                "version": plan.plan_version,
            },
            "teams": teams,
        }

    def events(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        records = self.coordinator.tool.tail_events(limit=limit)
        return [r.to_record() if hasattr(r, "to_record") else dict(r) for r in records]

    def list_escalations(self) -> List[Dict[str, Any]]:
        return [e.__dict__ for e in self.coordinator.escalations.list_open()]

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _to_report(result: CoordinatorTickResult) -> TickReport:
        return TickReport(
            ran_tasks=result.ran_tasks,
            approved=result.approved,
            rejected=result.rejected,
            notes=list(result.notes),
            spiral_team_ids=[v.team_id for v in result.spiral_verdicts if v.triggered],
            schedule_id=result.schedule_id,
            waves=result.waves,
            blocked=result.blocked,
        )

    @staticmethod
    def json_dumps(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
