"""Contract-first long-running run engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from ContractCoding.agents.profile import AgentProfileRegistry
from ContractCoding.config import Config
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.contract.compiler import ContractCompiler
from ContractCoding.contract.planner import ContractDraftPlanner, ContractDraftReviewer, PlanCritic
from ContractCoding.contract.spec import ContractSpec, ContractValidationError
from ContractCoding.contract.store import ContractFileStore
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.execution.runner import AgentRunner
from ContractCoding.runtime.health import HealthMonitor
from ContractCoding.runtime.hooks import HookManager
from ContractCoding.runtime.gate_runner import GateRunner
from ContractCoding.runtime.monitor import RunMonitor
from ContractCoding.runtime.recovery import RecoveryCoordinator
from ContractCoding.runtime.narrative import RunNarrativeBuilder
from ContractCoding.runtime.run_loop import RunLoop
from ContractCoding.runtime.settings import SettingsManager
from ContractCoding.runtime.store import RunRecord, RunStore
from ContractCoding.runtime.tasks import TaskIndex
from ContractCoding.runtime.teams import DependencyImpactAnalyzer, TeamRuntime
from ContractCoding.runtime.team_executor import StepExecutor, TeamExecutor
from ContractCoding.runtime.scheduler import Scheduler


@dataclass
class AutoRunResult:
    run_id: str
    status: str
    replans: int
    report: str
    task_id: str = ""


class AutoRunSteward:
    """Conservative autonomous driver for CLI-like runs."""

    TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "PAUSED"}

    def __init__(self, engine: "RunEngine"):
        self.engine = engine
        self.recovery = RecoveryCoordinator(engine)

    def run(
        self,
        task: str,
        *,
        contract_path: Optional[str] = None,
        max_steps: Optional[int] = None,
    ) -> AutoRunResult:
        run_id = self.engine.start(
            task,
            contract_path=contract_path,
            run_immediately=False,
        )
        return self.continue_run(run_id, max_steps=max_steps)

    def continue_run(
        self,
        run_id: str,
        *,
        max_steps: Optional[int] = None,
    ) -> AutoRunResult:
        replans = 0
        if max_steps is not None and max_steps <= 0:
            self.engine.store.update_run_status(
                run_id,
                "PAUSED",
                {"reason": "max_steps_reached", "max_steps": max_steps},
            )
            run = self.engine.store.get_run(run_id)
            if run:
                self.engine.task_index.sync_from_run(run, {"replans": replans})
            return AutoRunResult(
                run_id=run_id,
                status=run.status if run else "FAILED",
                replans=replans,
                report=self.engine.report(run_id),
                task_id=str((run.metadata if run else {}).get("task_id", "")),
            )

        loops = 0
        guardrails = self._effective_guardrails(run_id)
        starting_steps = self.engine.store.count_steps(run_id)
        while loops < self.engine.config.AUTO_MAX_STEWARD_LOOPS:
            loops += 1
            remaining_steps = None
            if max_steps is not None:
                consumed = self.engine.store.count_steps(run_id) - starting_steps
                remaining_steps = max_steps - consumed
                if remaining_steps <= 0:
                    self.engine.store.update_run_status(
                        run_id,
                        "PAUSED",
                        {
                            "reason": "max_steps_reached",
                            "max_steps": max_steps,
                            "steps_executed": consumed,
                        },
                    )
                    break
            run = self.engine.resume(run_id, max_steps=remaining_steps)
            if run.status in self.TERMINAL:
                break
            if run.status != "BLOCKED":
                break

            status = self.engine.status(run_id)
            health = status["health"]
            recovery = self.recovery.recover_without_replan(run_id, status, guardrails)
            if recovery:
                continue

            contract_replan_limit = int(guardrails.get("contract_replan_limit", self.engine.config.AUTO_CONTRACT_REPLAN_MAX))
            if not health.replan_recommended or replans >= contract_replan_limit:
                self.engine.store.update_run_status(
                    run_id,
                    "BLOCKED",
                    {
                        "needs_human": True,
                        "automatic_recovery_limit_reached": True,
                        "contract_replan_limit_reached": replans >= contract_replan_limit,
                    },
                )
                break

            feedback = self._diagnostic_feedback(status)
            self.engine.replan(run_id, feedback)
            replans += 1

        run = self.engine.store.get_run(run_id)
        if run and run.status == "RUNNING":
            if max_steps is not None:
                self.engine.store.update_run_status(
                    run_id,
                    "PAUSED",
                    {
                        "reason": "max_steps_reached",
                        "max_steps": max_steps,
                        "steps_executed": self.engine.store.count_steps(run_id) - starting_steps,
                    },
                )
            else:
                self.engine.store.update_run_status(
                    run_id,
                    "FAILED",
                    {"reason": "automatic steward loop limit reached"},
                )
            run = self.engine.store.get_run(run_id)
        result = AutoRunResult(
            run_id=run_id,
            status=run.status if run else "FAILED",
            replans=replans,
            report=self.engine.report(run_id),
            task_id=str((run.metadata if run else {}).get("task_id", "")),
        )
        if run:
            self.engine.task_index.sync_from_run(run, {"replans": replans})
        return result

    @staticmethod
    def _diagnostic_feedback(status: Dict[str, Any]) -> str:
        blocked = [
            f"{item.id}:{item.status}:{'; '.join(item.evidence[-3:])}"
            for item in status.get("work_items", [])
            if item.status == "BLOCKED"
        ]
        diagnostics = [
            f"{diagnostic.code}:{diagnostic.message}"
            for diagnostic in status.get("health").diagnostics
        ] if status.get("health") else []
        impact_tickets = [
            (
                f"{ticket.id}:{ticket.owner_scope or ticket.source_gate}:"
                f"{ticket.failure_summary}:{ticket.repair_instruction}"
            )
            for ticket in status.get("repair_tickets", [])
            if ticket.status in {"OPEN", "RUNNING"} and ticket.lane == "impact_replan"
        ]
        return (
            "Impact replan requested by AutoRunSteward. "
            "Apply the smallest contract delta that addresses the affected interface, "
            "acceptance scenario, owner hint, or team boundary. Do not rewrite unrelated teams. "
            "Preserve VERIFIED/DONE work. Reopen only blocked work. "
            f"Blocked items: {blocked or ['none']}. "
            f"Diagnostics: {diagnostics or ['none']}. "
            f"Impact repair tickets: {impact_tickets or ['none']}."
        )

    def _effective_guardrails(self, run_id: str) -> Dict[str, int]:
        contract = self.engine.store.get_contract(run_id)
        policy = (contract.execution_policy if contract else {}).get("autonomy_guardrails", {}) if contract else {}
        item_repair_limit = max(
            int(policy.get("item_repair_limit", self.engine.config.AUTO_ITEM_REPAIR_MAX)),
            int(self.engine.config.AUTO_ITEM_REPAIR_MAX),
        )
        return {
            "infra_retry_limit": max(
                int(policy.get("infra_retry_limit", self.engine.config.AUTO_INFRA_RETRY_MAX)),
                int(self.engine.config.AUTO_INFRA_RETRY_MAX),
            ),
            "item_repair_limit": item_repair_limit,
            "test_repair_limit": max(
                int(policy.get("test_repair_limit", max(item_repair_limit, self.engine.config.AUTO_TEST_REPAIR_MAX))),
                int(self.engine.config.AUTO_TEST_REPAIR_MAX),
                item_repair_limit,
            ),
            "contract_replan_limit": max(
                int(policy.get("contract_replan_limit", self.engine.config.AUTO_CONTRACT_REPLAN_MAX)),
                int(self.engine.config.AUTO_CONTRACT_REPLAN_MAX),
            ),
        }

class RunEngine:
    """Durable V4 control plane.

    The compiled contract is the scheduling source of truth. The store persists runtime
    state: statuses, steps, team runs, leases, events, and evidence.
    """

    def __init__(
        self,
        config: Config,
        agent_runner: Optional[AgentRunner] = None,
        context_manager: Optional[ContextManager] = None,
        store: Optional[RunStore] = None,
        profile_registry: Optional[AgentProfileRegistry] = None,
        step_executor: Optional[StepExecutor] = None,
        draft_planner: Optional[ContractDraftPlanner] = None,
    ):
        self.config = config
        self.agent_runner = agent_runner
        self.context_manager = context_manager
        self.store = store or RunStore.for_workspace(config.WORKSPACE_DIR, config.RUN_STORE_PATH)
        self.profile_registry = profile_registry or AgentProfileRegistry()
        self.compiler = ContractCompiler()
        self.draft_planner = draft_planner or ContractDraftPlanner.from_config(config)
        self.draft_reviewer = ContractDraftReviewer()
        self.plan_critic = PlanCritic()
        self.contract_files = ContractFileStore(config.WORKSPACE_DIR)
        self.settings_manager = SettingsManager(config)
        self.hooks = HookManager(store=self.store, enabled=True)
        self.task_index = TaskIndex(self.store)
        self.scheduler = Scheduler(self.store)
        self.run_loop = RunLoop(self)
        self.health_monitor = HealthMonitor(self.store, self.scheduler)
        self.narrative = RunNarrativeBuilder()
        self.monitor_builder = RunMonitor(self)
        self.team_runtime = TeamRuntime(config=config, store=self.store)
        self.dependency_impact = DependencyImpactAnalyzer(self.store)
        self.team_executor = TeamExecutor(
            config=config,
            store=self.store,
            agent_runner=agent_runner,
            context_manager=context_manager,
            profile_registry=self.profile_registry,
            step_executor=step_executor,
            team_runtime=self.team_runtime,
            hook_manager=self.hooks,
        )
        self.gate_runner = GateRunner(
            config=config,
            store=self.store,
            team_runtime=self.team_runtime,
            agent_runner=agent_runner,
            context_manager=context_manager,
            profile_registry=self.profile_registry,
            step_executor=step_executor,
            hook_manager=self.hooks,
        )
        self.auto_steward = AutoRunSteward(self)

    def plan(
        self,
        task: str,
        draft: Optional[ContractSpec | Dict[str, Any]] = None,
        write_files: bool = True,
    ) -> ContractSpec:
        if draft is None and self.config.LLM_PLANNER_ENABLED:
            draft_result = self.draft_planner.propose(task)
            if draft_result.ok:
                try:
                    review = self.draft_reviewer.review(draft_result.draft or {})
                    if not review.accepted:
                        self.hooks.emit(
                            "after_contract_drafted",
                            payload={
                                "backend": draft_result.backend,
                                "planner": "llm_draft",
                                "review": review.to_record(),
                                "fallback": "deterministic",
                            },
                        )
                        draft = None
                    else:
                        draft = draft_result.draft
                        self.hooks.emit(
                            "after_contract_drafted",
                            payload={
                                "backend": draft_result.backend,
                                "planner": "llm_draft",
                                "review": review.to_record(),
                            },
                        )
                except Exception as exc:
                    self.hooks.emit(
                        "after_contract_drafted",
                        payload={
                            "backend": draft_result.backend,
                            "planner": "llm_draft",
                            "review_error": str(exc),
                            "fallback": "deterministic",
                        },
                    )
                    draft = None
            elif draft_result.error:
                self.hooks.emit(
                    "after_contract_drafted",
                    payload={
                        "backend": draft_result.backend,
                        "planner": "llm_draft",
                        "error": draft_result.error,
                    },
                )
        try:
            contract = self.compiler.compile(task, draft)
        except ContractValidationError as exc:
            if draft is None:
                raise
            self.hooks.emit(
                "after_contract_drafted",
                payload={
                    "planner": "llm_draft",
                    "error": f"draft rejected by ContractCompiler: {exc}",
                    "fallback": "deterministic",
                },
            )
            draft = None
            contract = self.compiler.compile(task)
        if draft is not None:
            metadata = dict(contract.metadata)
            pipeline = list(metadata.get("planning_pipeline", []))
            if "llm_draft_planner" not in pipeline:
                pipeline.insert(0, "llm_draft_planner")
            metadata["planning_pipeline"] = pipeline
            metadata.setdefault("planner", "llm-draft+deterministic-compiler")
            contract = self._with_contract_metadata(contract, metadata)

            critic = self.plan_critic.review_contract(contract, context="llm_draft_plan")
            if not critic.accepted:
                self.hooks.emit(
                    "after_contract_drafted",
                    payload={
                        "planner": "llm_draft",
                        "plan_critic": critic.to_record(),
                        "fallback": "deterministic",
                    },
                )
                contract = self.compiler.compile(task)
                draft = None
            else:
                contract = self._with_plan_critic(contract, critic)

        if draft is None:
            critic = self.plan_critic.review_contract(contract, context="deterministic_plan")
            contract = self._with_plan_critic(contract, critic)
        if write_files:
            self.contract_files.write(contract)
        self.hooks.emit(
            "after_contract_compiled",
            payload={"contract_hash": contract.content_hash(), "task_intent": contract.metadata.get("task_intent", task)},
        )
        return contract

    def start(
        self,
        task: str,
        contract: Optional[ContractSpec] = None,
        contract_path: Optional[str] = None,
        initial_work_items: Optional[Iterable[WorkItem]] = None,
        run_immediately: bool = False,
        max_steps: Optional[int] = None,
        task_id: str = "",
    ) -> str:
        if contract_path:
            contract = self.contract_files.read(contract_path)
            contract = self.compiler.compile(task, contract)
            self.contract_files.write(contract)
        elif contract is None:
            if initial_work_items is not None:
                contract = self.compiler.compile(
                    task,
                    {
                        "goals": [task],
                        "work_scopes": [{"id": "root", "type": "root", "label": "Root work scope"}],
                        "work_items": [item.to_record() for item in initial_work_items],
                        "acceptance_criteria": ["All work items complete successfully."],
                    },
                )
            elif self.contract_files.exists():
                contract = self.contract_files.read()
                contract = self.compiler.compile(task, contract)
                self.contract_files.write(contract)
            else:
                contract = self.plan(task, write_files=True)
        else:
            contract = self.compiler.compile(task, contract)
            self.contract_files.write(contract)

        run_id = self.store.create_run(
            task=task,
            workspace_dir=self.config.WORKSPACE_DIR,
            contract=contract,
            metadata={"engine": "RunEngineV4", "contract_hash": contract.content_hash(), "task_id": task_id},
        )
        if task_id:
            self.task_index.attach_run(task_id, run_id, "PENDING")
        self.team_runtime.ensure_teams(run_id, contract)
        self.store.sync_gates(run_id, contract)
        self.hooks.emit(
            "after_contract_compiled",
            run_id=run_id,
            task_id=task_id,
            payload={"contract_hash": contract.content_hash(), "delivery_type": contract.metadata.get("delivery_type")},
        )
        if run_immediately:
            self.resume(run_id, max_steps=max_steps)
        return run_id

    def run_auto(
        self,
        task: str,
        *,
        contract_path: Optional[str] = None,
        max_steps: Optional[int] = None,
    ) -> AutoRunResult:
        existing = self.task_index.find_active_run_for_prompt(
            prompt=task,
            workspace_dir=self.config.WORKSPACE_DIR,
            backend=self.config.LLM_BACKEND,
        )
        if existing is not None:
            result = self.auto_steward.continue_run(existing.active_run_id, max_steps=max_steps)
            result.task_id = existing.id
            run = self.store.get_run(existing.active_run_id)
            if run:
                self.task_index.sync_from_run(run, {"resumed_existing_run": True, "replans": result.replans})
            return result
        task_record = self.task_index.create(
            prompt=task,
            workspace_dir=self.config.WORKSPACE_DIR,
            backend=self.config.LLM_BACKEND,
        )
        run_id = self.start(task, contract_path=contract_path, run_immediately=False, task_id=task_record.id)
        result = self.auto_steward.continue_run(run_id, max_steps=max_steps)
        result.task_id = task_record.id
        run = self.store.get_run(run_id)
        if run:
            self.task_index.sync_from_run(run, {"replans": result.replans})
        return result

    def resume_auto(self, run_id: str, max_steps: Optional[int] = None) -> AutoRunResult:
        return self.auto_steward.continue_run(self.resolve_run_id(run_id), max_steps=max_steps)

    def find_active_run_for_task(self, task: str) -> str:
        task_record = self.task_index.find_active_run_for_prompt(
            prompt=task,
            workspace_dir=self.config.WORKSPACE_DIR,
            backend=self.config.LLM_BACKEND,
        )
        return task_record.active_run_id if task_record is not None else ""

    def resume(self, run_id: str, max_steps: Optional[int] = None) -> RunRecord:
        return self.run_loop.resume(run_id, max_steps=max_steps)

    def replan(self, run_id: str, feedback: str) -> ContractSpec:
        run_id = self.resolve_run_id(run_id)
        run = self._require_run(run_id)
        current = self.store.get_contract(run_id)
        if current is None:
            raise ValueError(f"Run {run_id} does not have a compiled contract to replan.")
        revised = self.compiler.replan(run.task, current, feedback)
        revised = self._merge_replan_runtime_state(run_id, revised)
        critic = self.plan_critic.review_contract(revised, context="replan")
        revised = self._with_plan_critic(revised, critic, metadata_key="replan_critic")
        self.store.save_contract_version(run_id, revised)
        self.store.sync_work_items(run_id, revised.work_items)
        self.team_runtime.ensure_teams(run_id, revised)
        self.store.sync_gates(run_id, revised)
        self.store.append_event(
            run_id,
            "run_replanned",
            {
                "contract_hash": revised.content_hash(),
                "revision": revised.metadata.get("revision", 0),
                "feedback": feedback,
            },
        )
        for ticket in self.store.list_repair_tickets(run_id, statuses={"OPEN", "RUNNING"}, limit=200):
            if ticket.lane == "impact_replan":
                self.store.update_repair_ticket_status(
                    ticket.id,
                    "RESOLVED",
                    evidence_refs=[revised.content_hash()],
                    metadata={"resolved_by": "contract_delta_replan"},
                )
        self.contract_files.write(revised)
        self.store.update_run_status(run_id, "RUNNING")
        return revised

    def _merge_replan_runtime_state(self, run_id: str, revised: ContractSpec) -> ContractSpec:
        runtime_by_id = {item.id: item for item in self.store.list_work_items(run_id)}
        merged_items = []
        for item in revised.work_items:
            existing = runtime_by_id.get(item.id)
            if existing is None:
                merged_items.append(item)
                continue
            payload = item.to_record()
            payload["evidence"] = list(existing.evidence)
            if existing.status == "BLOCKED":
                payload["status"] = "READY"
                inputs = dict(existing.inputs)
                inputs.update(payload.get("inputs", {}))
                inputs["replan_feedback"] = revised.metadata.get("replan_feedback", "")
                payload["inputs"] = inputs
            else:
                payload["status"] = existing.status
                payload["inputs"] = {**dict(existing.inputs), **dict(payload.get("inputs", {}))}
            merged_items.append(WorkItem.from_mapping(payload))
        return ContractSpec(
            goals=revised.goals,
            work_scopes=revised.work_scopes,
            work_items=merged_items,
            requirements=revised.requirements,
            architecture=revised.architecture,
            milestones=revised.milestones,
            phase_plan=revised.phase_plan,
            interfaces=revised.interfaces,
            deltas=revised.deltas,
            team_gates=revised.team_gates,
            final_gate=revised.final_gate,
            acceptance_criteria=revised.acceptance_criteria,
            execution_policy=revised.execution_policy,
            risk_policy=revised.risk_policy,
            verification_policy=revised.verification_policy,
            test_ownership=revised.test_ownership,
            version=revised.version,
            metadata=revised.metadata,
            owner_hints=revised.owner_hints,
        )

    def _with_plan_critic(
        self,
        contract: ContractSpec,
        critic,
        *,
        metadata_key: str = "plan_critic",
    ) -> ContractSpec:
        metadata = dict(contract.metadata)
        metadata[metadata_key] = critic.to_record()
        pipeline = list(metadata.get("planning_pipeline", []))
        if "plan_critic" not in pipeline:
            pipeline.append("plan_critic")
        metadata["planning_pipeline"] = pipeline
        return self._with_contract_metadata(contract, metadata)

    @staticmethod
    def _with_contract_metadata(contract: ContractSpec, metadata: Dict[str, Any]) -> ContractSpec:
        return ContractSpec(
            goals=contract.goals,
            work_scopes=contract.work_scopes,
            work_items=contract.work_items,
            requirements=contract.requirements,
            architecture=contract.architecture,
            milestones=contract.milestones,
            phase_plan=contract.phase_plan,
            interfaces=contract.interfaces,
            deltas=contract.deltas,
            team_gates=contract.team_gates,
            final_gate=contract.final_gate,
            acceptance_criteria=contract.acceptance_criteria,
            execution_policy=contract.execution_policy,
            risk_policy=contract.risk_policy,
            verification_policy=contract.verification_policy,
            test_ownership=contract.test_ownership,
            version=contract.version,
            metadata=dict(metadata),
            owner_hints=contract.owner_hints,
        )

    def pause(self, run_id: str) -> None:
        run_id = self.resolve_run_id(run_id)
        self._require_run(run_id)
        self.store.update_run_status(run_id, "PAUSED")

    def cancel(self, run_id: str) -> None:
        run_id = self.resolve_run_id(run_id)
        self._require_run(run_id)
        self.store.update_run_status(run_id, "CANCELLED")

    def status(self, run_id: str) -> Dict[str, Any]:
        run_id = self.resolve_run_id(run_id)
        run = self._require_run(run_id)
        return {
            "run": run,
            "task": self.task_index.task_for_run(run_id),
            "contract": self.store.get_contract(run_id),
            "work_items": self.store.list_work_items(run_id),
            "steps": self.store.latest_steps(run_id, limit=20),
            "team_runs": self.store.list_wave_team_runs(run_id, limit=20),
            "scope_teams": self.store.list_scope_team_runs(run_id, limit=200),
            "gates": self.store.list_gates(run_id),
            "repair_tickets": self.store.list_repair_tickets(run_id, limit=200),
            "events": self.store.list_events(run_id, limit=20),
            "blocked": self.scheduler.blocked_reasons(run_id),
            "health": self.health_monitor.check(run_id),
        }

    def report(self, run_id: str, max_lines: int = 12) -> str:
        run_id = self.resolve_run_id(run_id)
        status = self.status(run_id)
        run = status["run"]
        return self.narrative.build_report(
            run=run,
            items=status["work_items"],
            steps=status["steps"],
            team_runs=[*status.get("scope_teams", []), *status["team_runs"]],
            gates=status.get("gates", []),
            repair_tickets=status.get("repair_tickets", []),
            contract=status.get("contract"),
            waves=self.scheduler.next_wave(run_id),
            health=status.get("health"),
            max_lines=max_lines,
        )

    def monitor(self, run_id: str, write_file: bool = True) -> Dict[str, Any]:
        return self.monitor_builder.snapshot(run_id, write_file=write_file)

    def graph(self, run_id: str) -> Dict[str, Any]:
        run_id = self.resolve_run_id(run_id)
        contract = self.store.get_contract(run_id)
        if contract is None:
            raise ValueError(f"Run {run_id} does not have a compiled contract.")
        ready = self.scheduler.next_wave(run_id)
        blocked = self.scheduler.blocked_reasons(run_id)
        return {
            "contract_hash": contract.content_hash(),
            "scopes": [scope.to_record() for scope in contract.work_scopes],
            "items": [item.to_record() for item in self.store.list_work_items(run_id)],
            "teams": [
                {
                    "team_id": team.id,
                    "scope_id": team.scope_id,
                    "status": team.status,
                    "team_kind": team.metadata.get("team_kind"),
                    "workspace_plane": team.execution_plane,
                    "roles": team.metadata.get("roles", []),
                    "owned_items": team.work_item_ids,
                    "owned_artifacts": team.metadata.get("owned_artifacts", []),
                    "promotion_policy": team.metadata.get("promotion_policy", {}),
                    "active_items": team.metadata.get("active_items", []),
                    "partial_promoted_files": team.metadata.get("partial_promoted_files", []),
                    "blocked_reason": team.metadata.get("promotion_error") or team.metadata.get("stale_reason") or "",
                }
                for team in self.store.list_scope_team_runs(run_id, limit=200)
            ],
            "gates": [
                {
                    "gate_id": gate.gate_id,
                    "gate_type": gate.gate_type,
                    "scope_id": gate.scope_id,
                    "status": gate.status,
                    "evidence": gate.evidence[-5:],
                    "metadata": gate.metadata,
                }
                for gate in self.store.list_gates(run_id)
            ],
            "repair_tickets": [
                {
                    "id": ticket.id,
                    "lane": ticket.lane,
                    "status": ticket.status,
                    "source_gate": ticket.source_gate,
                    "source_item_id": ticket.source_item_id,
                    "owner_scope": ticket.owner_scope,
                    "owner_artifacts": ticket.owner_artifacts,
                    "affected_scopes": ticket.affected_scopes,
                    "attempt_count": ticket.attempt_count,
                    "summary": ticket.failure_summary,
                    "repair_bundle": ticket.metadata.get("repair_bundle", {}),
                }
                for ticket in self.store.list_repair_tickets(run_id, limit=200)
            ],
            "ready_waves": [
                {
                    "scope_id": wave.scope.id,
                    "wave_kind": wave.wave_kind,
                    "execution_plane": wave.execution_plane,
                    "parallel_slots": wave.parallel_slots,
                    "items": [item.id for item in wave.items],
                    "conflict_keys": wave.conflict_keys,
                    "parallel_reason": wave.parallel_reason,
                    "serial_reason": wave.serial_reason,
                }
                for wave in ready
            ],
            "blocked": [{"work_item_id": item.work_item_id, "reason": item.reason} for item in blocked],
        }

    def teams(self, run_id: str) -> List[Dict[str, Any]]:
        run_id = self.resolve_run_id(run_id)
        self._require_run(run_id)
        return [
            {
                "team_id": team.id,
                "scope_id": team.scope_id,
                "status": team.status,
                "team_kind": team.metadata.get("team_kind"),
                "workspace_plane": team.execution_plane,
                "roles": team.metadata.get("roles", []),
                "owned_items": team.work_item_ids,
                "owned_artifacts": team.metadata.get("owned_artifacts", []),
                "promotion_policy": team.metadata.get("promotion_policy", {}),
                "team_memory": team.metadata.get("team_memory", {}),
                "active_items": team.metadata.get("active_items", []),
                "partial_promoted_files": team.metadata.get("partial_promoted_files", []),
                "promoted_files": team.metadata.get("promoted_files", []),
                "blocked_reason": team.metadata.get("promotion_error") or team.metadata.get("stale_reason") or "",
            }
            for team in self.store.list_scope_team_runs(run_id, limit=200)
        ]

    def events(self, run_id: str, limit: int = 50):
        run_id = self.resolve_run_id(run_id)
        self._require_run(run_id)
        return self.store.list_events(run_id, limit=limit)

    def human_events(self, run_id: str, limit: int = 50) -> list[str]:
        run_id = self.resolve_run_id(run_id)
        self._require_run(run_id)
        return self.narrative.events_to_human(self.store.list_events(run_id, limit=limit))

    def resolve_run_id(self, task_or_run_id: str) -> str:
        return self.task_index.resolve_run_id(task_or_run_id)

    def _execute_wave(self, run: RunRecord, wave) -> Any:
        self.hooks.emit(
            "before_team_dispatch",
            run_id=run.id,
            task_id=str(run.metadata.get("task_id", "")),
            payload={"scope_id": wave.scope.id, "items": [item.id for item in wave.items]},
        )
        return self.team_executor.execute(run, wave)

    def _refresh_runtime_settings(self) -> None:
        settings = self.settings_manager.snapshot()
        self.settings_manager.apply_to_config(settings)
        self.hooks.enabled = settings.hooks_enabled
        self.scheduler.runtime_overrides = settings.scheduler_overrides()

    def _finish_or_block(self, run_id: str) -> None:
        self._promote_ready_teams(run_id)
        self._resolve_repair_tickets(run_id)
        items = self.store.list_work_items(run_id)
        gates = self.store.list_gates(run_id)
        final_gate = next((gate for gate in gates if gate.gate_id == "final"), None)
        all_team_gates_passed = all(gate.status == "PASSED" for gate in gates if gate.gate_type == "team")
        if (
            items
            and all(item.status == "VERIFIED" for item in items)
            and self._teams_complete(run_id)
            and all_team_gates_passed
            and (final_gate is None or final_gate.status == "PASSED")
        ):
            self.store.update_run_status(
                run_id,
                "COMPLETED",
                {
                    "needs_human": False,
                    "automatic_recovery_limit_reached": False,
                    "contract_replan_limit_reached": False,
                    "replan_recommended": False,
                    "replan_reasons": [],
                },
            )
            run = self._require_run(run_id)
            self.task_index.sync_from_run(run)
            self.hooks.emit(
                "after_run_complete",
                run_id=run_id,
                task_id=str(run.metadata.get("task_id", "")),
                payload={"status": run.status},
            )
        elif items:
            failed = [item for item in items if item.status == "BLOCKED"]
            failed_gates = [gate for gate in gates if gate.status in {"FAILED", "BLOCKED"}]
            metadata = {}
            if failed or failed_gates:
                metadata = {
                    "replan_recommended": True,
                    "replan_reasons": [
                        *[f"{item.id}:{item.status}" for item in failed],
                        *[f"{gate.gate_id}:{gate.status}" for gate in failed_gates],
                    ],
                }
                self.store.append_event(run_id, "replan_recommended", metadata)
            elif all(item.status == "VERIFIED" for item in items):
                incomplete_teams = [
                    team
                    for team in self.store.list_scope_team_runs(run_id, limit=200)
                    if team.status not in {"PROMOTED", "CLOSED"}
                ]
                if incomplete_teams:
                    metadata = {
                        "replan_recommended": False,
                        "replan_reasons": [
                            f"team:{team.scope_id}:{team.status}" for team in incomplete_teams
                        ],
                    }
            self.store.update_run_status(run_id, "BLOCKED", metadata)
            run = self._require_run(run_id)
            self.task_index.sync_from_run(run, metadata)
            self.hooks.emit(
                "on_blocked",
                run_id=run_id,
                task_id=str(run.metadata.get("task_id", "")),
                payload=metadata,
            )
        else:
            self.store.update_run_status(run_id, "FAILED", {"reason": "run has no work items"})
            run = self._require_run(run_id)
            self.task_index.sync_from_run(run, {"reason": "run has no work items"})

    def _resolve_repair_tickets(self, run_id: str) -> None:
        for ticket in self.store.list_repair_tickets(run_id, statuses={"OPEN", "RUNNING"}, limit=200):
            if ticket.lane == "impact_replan":
                continue
            if ticket.source_item_id:
                item = self.store.get_work_item(run_id, ticket.source_item_id)
                if item is not None and item.status == "VERIFIED":
                    self.store.update_repair_ticket_status(
                        ticket.id,
                        "RESOLVED",
                        evidence_refs=[ticket.source_item_id],
                        metadata={"resolved_by": "work_item_verified"},
                    )
                continue
            if ticket.source_gate:
                gate = self.store.get_gate(run_id, ticket.source_gate)
                if gate is not None and gate.status == "PASSED":
                    self.store.update_repair_ticket_status(
                        ticket.id,
                        "RESOLVED",
                        evidence_refs=[ticket.source_gate],
                        metadata={"resolved_by": "gate_passed"},
                    )

    def _promote_ready_teams(self, run_id: str) -> None:
        run = self._require_run(run_id)
        contract = self.store.get_contract(run_id)
        if contract is None:
            return
        self.team_runtime.ensure_teams(run_id, contract)
        for team in self.store.list_scope_team_runs(run_id, limit=200):
            if team.scope_id == "integration":
                continue
            self.team_runtime.promote_verified_artifacts(run, contract, team.scope_id)
            promoted = self.team_runtime.promote_if_ready(run, contract, team.scope_id)
            if promoted and team.scope_id != "package":
                self.dependency_impact.mark_stale(
                    run_id,
                    team.scope_id,
                    reason="dependent runtime gate must revalidate after promotion",
                )

    def _teams_complete(self, run_id: str) -> bool:
        teams = [
            team
            for team in self.store.list_scope_team_runs(run_id, limit=200)
            if team.scope_id != "integration"
        ]
        if not teams:
            return True
        return all(team.status in {"PROMOTED", "CLOSED"} for team in teams)

    def _require_run(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown run id: {run_id}")
        return run
