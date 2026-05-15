"""ProjectCoordinator — onboarding, plan-freeze, orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ..contract.capsule import CapsuleStatus, CapsuleTag, InterfaceCapsuleV2
from ..contract.kernel import ContractKernel, ProjectContract, TeamContract
from ..contract.operation import ContractOperation, OperationKind, OperationStatus
from ..contract.evidence import ValidationEvidence
from ..contract.project import BoundedContext, IntentLedger, Invariant, PlanSpec
from ..contract.team import TeamSubContract, WorkingPaper
from ..contract.work import TeamScheduleReport, TeamWorkItem, WorkPhase, WorkStatus
from ..core.events import EventKind
from ..core.margin import AgentRole
from ..memory.ledgers import TaskItem, TaskLedger, TaskStatus
from ..registry import Actor, RegistryACL, RegistryBackend, RegistryTool
from ..worker import (
    ContextPacket,
    PipelineResult,
    WorkerPipeline,
)
from ..worker.protocol import LLMPort
from .escalation import DeathSpiralDetector, EscalationQueue, SpiralVerdict
from .auditor import ContractAuditor, split_capsule_ref
from .reducer import ContractReducer
from .scheduler import SchedulerConfig, TeamScheduler


@dataclass
class TeamTools:
    steward: RegistryTool
    planner: RegistryTool
    inspector: RegistryTool
    implementer: RegistryTool
    reviewer: RegistryTool
    judge: RegistryTool


def make_team_tools(
    backend: RegistryBackend,
    acl: RegistryACL,
    team_id: str,
    *,
    agent_prefix: str = "",
) -> TeamTools:
    prefix = agent_prefix or team_id

    def _t(role: AgentRole, suffix: str) -> RegistryTool:
        return RegistryTool(
            backend=backend,
            acl=acl,
            actor=Actor(agent_id=f"{prefix}:{suffix}", role=role, team_id=team_id),
        )

    return TeamTools(
        steward=_t(AgentRole.STEWARD, "steward"),
        planner=_t(AgentRole.PLANNER, "planner"),
        inspector=_t(AgentRole.INSPECTOR, "inspector"),
        implementer=_t(AgentRole.IMPLEMENTER, "implementer"),
        reviewer=_t(AgentRole.REVIEWER, "reviewer"),
        judge=_t(AgentRole.JUDGE, "judge"),
    )


@dataclass
class CoordinatorTickResult:
    ran_tasks: int
    approved: int
    rejected: int
    spiral_verdicts: List[SpiralVerdict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    schedule_id: str = ""
    waves: int = 0
    blocked: int = 0


class ProjectCoordinator:
    """Top-level orchestrator. Stateless across calls; persists via registry."""

    def __init__(
        self,
        backend: RegistryBackend,
        acl: Optional[RegistryACL] = None,
        *,
        coordinator_id: str = "coordinator",
        max_item_repair_attempts: int = 1,
    ):
        self.backend = backend
        self.acl = acl or RegistryACL()
        self.coordinator_id = coordinator_id
        self.tool = RegistryTool(
            backend=backend,
            acl=self.acl,
            actor=Actor.coordinator(coordinator_id),
        )
        self.escalations = EscalationQueue(self.tool)
        self.spiral = DeathSpiralDetector(self.tool)
        self.auditor = ContractAuditor(self.tool)
        self.reducer = ContractReducer(self.tool, self.auditor)
        self.scheduler = TeamScheduler(SchedulerConfig())
        self.max_item_repair_attempts = max(0, int(max_item_repair_attempts))
        self._last_poll_ts: float = 0.0

    # ============================================================ onboarding

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
        intent = IntentLedger(
            goal=goal,
            acceptance_signals=list(acceptance_signals or []),
            non_goals=list(non_goals or []),
            assumptions=list(assumptions or []),
        )
        plan = PlanSpec(
            intent=intent,
            bounded_contexts=list(bounded_contexts),
            cross_team_invariants=list(invariants or []),
            plan_version=plan_version,
        )
        errors = plan.validate()
        if errors:
            raise ValueError(f"onboarding plan invalid: {errors}")
        written = self.tool.write_plan(plan, freeze=freeze)
        self.tool.write_project_contract(ProjectContract.from_plan(written))
        return written

    def activate_team(
        self,
        team_id: str,
        *,
        initial_tasks: Optional[List[TaskItem]] = None,
    ) -> TeamSubContract:
        plan = self.tool.get_plan()
        if plan is None:
            raise RuntimeError("plan not yet onboarded")
        ctx = plan.context_of(team_id)
        bootstrap_tool = RegistryTool(
            backend=self.backend,
            acl=self.acl,
            actor=Actor(
                agent_id=f"{self.coordinator_id}-bootstrap:{team_id}",
                role=AgentRole.PLANNER,
                team_id=team_id,
            ),
        )
        wp = WorkingPaper(
            team_id=team_id,
            bounded_context_purpose=ctx.purpose_one_liner,
            owned_invariants=[
                inv.id for inv in plan.cross_team_invariants
                if inv.scope in ("global", f"team:{team_id}")
            ],
        )
        bootstrap_tool.write_working_paper(wp)

        ledger = TaskLedger(
            team_id=team_id,
            items=list(initial_tasks or []),
        )
        bootstrap_tool.write_task_ledger(ledger)

        bootstrap_tool.emit_event(
            EventKind.TEAM_ACTIVATED,
            team_id=team_id,
            payload={
                "purpose": ctx.purpose_one_liner,
                "capability_names": ctx.capability_names,
                "n_tasks": len(ledger.items),
            },
        )
        subcontract = bootstrap_tool.get_team_subcontract(team_id)
        self.tool.write_team_contract(TeamContract.from_subcontract(subcontract))
        return subcontract

    # =================================================== capsule lifecycle

    def lock_capsule(
        self,
        team_id: str,
        capability: str,
        *,
        consumer_teams: Optional[Iterable[str]] = None,
        evidence: Optional[List[str]] = None,
    ) -> bool:
        capsule = self.tool.get_capsule(team_id, capability)
        if capsule is None:
            return False
        for c in consumer_teams or []:
            self.tool.add_consumer(team_id, capability, c)
        capsule = self.tool.get_capsule(team_id, capability)
        if capsule is None:
            return False
        result = self.tool.publish_capsule(
            capsule,
            target_status=CapsuleStatus.LOCKED,
            reason="coordinator lock",
            evidence=evidence or [],
        )
        return result.ok

    # ================================================================ tick

    def run_once(
        self,
        *,
        pipelines: Dict[str, WorkerPipeline],
        max_per_team: int = 1,
    ) -> CoordinatorTickResult:
        project = self.tool.get_project_contract()
        if project is None or not project.frozen:
            return CoordinatorTickResult(
                ran_tasks=0, approved=0, rejected=0,
                notes=["project contract not yet frozen"],
            )
        plan = project.to_plan()

        events = self.tool.tail_events(since_ts=self._last_poll_ts)
        self._last_poll_ts = time.time()
        ran = 0
        approved = 0
        rejected = 0
        notes: List[str] = [f"polled {len(events)} events"]

        self._reduce_pending_operations()
        teams = self._load_team_contracts(project)
        capsules = self._load_capsule_index()
        kernel = ContractKernel(
            project=project,
            teams=teams,
            capsules=capsules,
            obligations=[],
        )
        derived = self.auditor.derive_obligations(kernel)
        accepted_ops = [
            op
            for op in self.tool.read_contract_operations()
            if op.status == OperationStatus.ACCEPTED
        ]
        obligations = self.auditor.resolve_obligations(derived, accepted_ops)
        self.tool.write_obligations(obligations)
        kernel.obligations = obligations

        schedule = self.scheduler.schedule(kernel, max_per_team=max_per_team)
        self.tool.append_schedule(schedule)
        notes.extend(schedule.notes)

        first_wave = schedule.waves[0] if schedule.waves else None
        if first_wave is not None:
            self._declare_ready_dependencies(first_wave.items)
            wave_results = self._run_wave(plan, first_wave.items, pipelines)
            for work, result in wave_results:
                ran += 1
                if result.verdict.approved:
                    approved += 1
                    self._record_success_operation(work, result)
                else:
                    rejected += 1
                    self._record_blocker_operation(work, result)
                self._sync_work_item_after_run(work, result)

        spiral_verdicts = self.spiral.scan_all(plan.team_ids())
        return CoordinatorTickResult(
            ran_tasks=ran,
            approved=approved,
            rejected=rejected,
            spiral_verdicts=spiral_verdicts,
            notes=notes,
            schedule_id=schedule.schedule_id,
            waves=len(schedule.waves),
            blocked=len(schedule.blocked),
        )

    # ----- helpers -----

    def _reduce_pending_operations(self) -> None:
        operations = self.tool.read_contract_operations()
        self.reducer.apply_pending(operations)

    def _load_team_contracts(self, project: ProjectContract) -> Dict[str, TeamContract]:
        teams: Dict[str, TeamContract] = {}
        for ctx in project.bounded_contexts:
            contract = self.tool.get_team_contract(ctx.team_id)
            if contract is None:
                subcontract = self.tool.get_team_subcontract(ctx.team_id)
                contract = TeamContract.from_subcontract(subcontract)
                self.tool.write_team_contract(contract)
            teams[ctx.team_id] = contract
        return teams

    def _load_capsule_index(self) -> Dict[str, InterfaceCapsuleV2]:
        out: Dict[str, InterfaceCapsuleV2] = {}
        for cap in self.tool.list_capsules():
            out[f"{cap.team_id}/{cap.capability}"] = cap
        return out

    def _declare_ready_dependencies(self, items: List[TeamWorkItem]) -> None:
        for item in items:
            for cap_ref in item.capsule_dependencies:
                owner, capability = split_capsule_ref(cap_ref)
                if not owner or not capability:
                    continue
                cap = self.tool.get_capsule(owner, capability)
                if cap is None or item.team_id in cap.consumers:
                    continue
                op = ContractOperation.new(
                    kind=OperationKind.DECLARE_DEPENDENCY,
                    from_team=item.team_id,
                    target_team=owner,
                    target_ref=f"{owner}/{capability}",
                    related_task_ids=[item.task_id],
                    evidence_refs=[f"contract:work:{item.work_id}"],
                    payload={"capability": capability},
                    rationale="declared dependency from scheduled work item",
                )
                self.reducer.process(op)

    def _run_wave(
        self,
        plan: PlanSpec,
        items: List[TeamWorkItem],
        pipelines: Dict[str, WorkerPipeline],
    ) -> List[tuple[TeamWorkItem, PipelineResult]]:
        results: List[tuple[TeamWorkItem, PipelineResult]] = []
        if not items:
            return results
        max_workers = max(1, min(len(items), self.scheduler.config.max_parallel_teams))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {}
            for item in items:
                pipeline = pipelines.get(item.team_id)
                if pipeline is None:
                    self._block_unscheduled_item(item, f"no pipeline for {item.team_id}")
                    continue
                try:
                    ctx = plan.context_of(item.team_id)
                except KeyError:
                    self._block_unscheduled_item(item, f"no bounded context for {item.team_id}")
                    continue
                packet = self._build_packet(plan, ctx, item.team_id, item)
                if packet is None:
                    self._block_unscheduled_item(item, f"failed packet for {item.team_id}/{item.task_id}")
                    continue
                future_map[pool.submit(pipeline.run, packet)] = item
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    results.append((item, future.result()))
                except Exception as exc:  # pragma: no cover - defensive
                    self._block_unscheduled_item(item, f"pipeline errored: {exc!r}")
        return results

    def _block_unscheduled_item(self, item: TeamWorkItem, reason: str) -> None:
        item.status = WorkStatus.BLOCKED
        op = ContractOperation.new(
            kind=OperationKind.REPORT_BLOCKER,
            from_team=item.team_id,
            target_ref=f"work:{item.work_id}",
            related_task_ids=[item.task_id],
            payload={"reason": reason},
        )
        self.reducer.process(op)

    def _record_success_operation(self, work: TeamWorkItem, result: PipelineResult) -> None:
        artifact_refs = [f"workspace/{work.team_id}/{art.path}" for art in result.packet.artifacts]
        evidence_refs: List[str] = []
        judge_evidence = ValidationEvidence.new(
            team_id=work.team_id,
            work_id=work.work_id,
            command="worker:judge",
            passed=True,
            artifact_refs=artifact_refs,
        )
        self.tool.append_validation_evidence(judge_evidence)
        evidence_refs.append(judge_evidence.ref())
        if result.verdict.smoke_passed is True:
            smoke_evidence = ValidationEvidence.new(
                team_id=work.team_id,
                work_id=work.work_id,
                command="smoke",
                passed=True,
                artifact_refs=artifact_refs,
            )
            self.tool.append_validation_evidence(smoke_evidence)
            evidence_refs.append(smoke_evidence.ref())
        op = ContractOperation.new(
            kind=OperationKind.SUBMIT_EVIDENCE,
            from_team=work.team_id,
            target_ref=f"work:{work.work_id}",
            related_task_ids=[work.task_id],
            evidence_refs=evidence_refs,
            payload={
                "approved": True,
                "reasons": list(result.verdict.reasons),
                "artifact_refs": artifact_refs,
                "change_set": (
                    result.packet.change_set.to_record()
                    if result.packet.change_set is not None
                    else None
                ),
            },
            rationale="worker verdict approved",
        )
        self.reducer.process(op)

    def _record_blocker_operation(self, work: TeamWorkItem, result: PipelineResult) -> None:
        op = ContractOperation.new(
            kind=OperationKind.REPORT_BLOCKER,
            from_team=work.team_id,
            target_ref=f"work:{work.work_id}",
            related_task_ids=[work.task_id],
            payload={"blockers": list(result.verdict.blockers)},
            rationale="worker verdict rejected",
        )
        self.reducer.process(op)

    def _sync_work_item_after_run(self, work: TeamWorkItem, result: PipelineResult) -> None:
        contract = self.tool.get_team_contract(work.team_id)
        if contract is None:
            return
        stored = contract.by_work_id(work.work_id) or contract.by_task_id(work.task_id)
        if stored is None:
            stored = work
        stored.attempts = int(getattr(result.packet.task, "attempts", stored.attempts))
        if result.verdict.approved:
            stored.status = WorkStatus.DONE
        elif self._should_retry_failed_work(stored, result):
            stored.status = WorkStatus.PENDING
            stored.phase = WorkPhase.REPAIR
            team_tool = self._team_control_tool(work.team_id)
            team_tool.set_task_status(work.team_id, work.task_id, TaskStatus.PENDING)
            team_tool.append_progress(
                work.team_id,
                task_id=work.task_id,
                kind="coordinator",
                summary=f"queued repair retry {stored.attempts}/{self.max_item_repair_attempts}",
                payload={"blockers": list(result.verdict.blockers)},
            )
        else:
            stored.status = WorkStatus.BLOCKED
        if result.packet.artifacts:
            stored.writes = [art.path for art in result.packet.artifacts]
        contract.upsert_work_item(stored)
        self.tool.write_team_contract(contract)

    def _should_retry_failed_work(
        self,
        work: TeamWorkItem,
        result: PipelineResult,
    ) -> bool:
        if self.max_item_repair_attempts <= 0:
            return False
        if int(work.attempts) > self.max_item_repair_attempts:
            return False
        text = " | ".join(str(b).lower() for b in result.verdict.blockers)
        retryable_markers = (
            "no artifacts produced",
            "artifact outside declared writes",
            "missing declared artifacts",
            "smoke tests failing",
            "write conflict",
        )
        return any(marker in text for marker in retryable_markers)

    def _team_control_tool(self, team_id: str) -> RegistryTool:
        return RegistryTool(
            backend=self.backend,
            acl=self.acl,
            actor=Actor(
                agent_id=f"{self.coordinator_id}:repair:{team_id}",
                role=AgentRole.STEWARD,
                team_id=team_id,
            ),
        )

    def _build_packet(
        self,
        plan: PlanSpec,
        ctx: BoundedContext,
        team_id: str,
        task: Any,
    ) -> Optional[ContextPacket]:
        subcontract = self.tool.get_team_subcontract(team_id)
        return ContextPacket(
            plan=plan,
            bounded_context=ctx,
            subcontract=subcontract,
            task=task,
            work_item=task if isinstance(task, TeamWorkItem) else None,
        )

    def propose_capsule_skeleton(
        self,
        team_id: str,
        capability: str,
        purpose: str,
        *,
        key_capabilities: Optional[List[str]] = None,
    ) -> InterfaceCapsuleV2:
        capsule_id = f"cap:{team_id}:{capability}:{uuid.uuid4().hex[:6]}"
        return InterfaceCapsuleV2(
            capsule_id=capsule_id,
            team_id=team_id,
            capability=capability,
            tag=CapsuleTag(
                name=capability,
                one_line_purpose=purpose,
                key_capabilities=list(key_capabilities or []),
            ),
            status=CapsuleStatus.PROPOSED,
        )
