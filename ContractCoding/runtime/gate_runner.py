"""First-class team and final gate runtime."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Dict, Iterable, List, Optional

from ContractCoding.agents.profile import AgentProfileRegistry
from ContractCoding.config import Config
from ContractCoding.contract.spec import ContractSpec, TeamGateSpec
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.execution.runner import AgentRunner
from ContractCoding.execution.workspace import workspace_scope
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.llm.observability import payload_observability
from ContractCoding.quality.failure_router import FailureRouter
from ContractCoding.quality.diagnostics import DiagnosticBuilder, DiagnosticRecord
from ContractCoding.quality.gates import GateChecker
from ContractCoding.quality.review import GateReviewParser
from ContractCoding.quality.self_check import SelfChecker
from ContractCoding.runtime.hooks import HookManager
from ContractCoding.runtime.store import RunRecord, RunStore
from ContractCoding.runtime.team_executor import StepExecutor, TeamExecutor
from ContractCoding.runtime.teams import TeamRuntime
from ContractCoding.runtime.test_strata import TestStrataAuditor
from ContractCoding.utils.state import GeneralState


@dataclass
class GateRunResult:
    gate_id: str
    ran: bool = False
    ok: bool = True
    error: str = ""


class GateRunner:
    def __init__(
        self,
        *,
        config: Config,
        store: RunStore,
        team_runtime: TeamRuntime,
        agent_runner: Optional[AgentRunner] = None,
        context_manager: Optional[ContextManager] = None,
        profile_registry: Optional[AgentProfileRegistry] = None,
        step_executor: Optional[StepExecutor] = None,
        hook_manager: Optional[HookManager] = None,
    ):
        self.config = config
        self.store = store
        self.team_runtime = team_runtime
        self.agent_runner = agent_runner
        self.context_manager = context_manager
        self.profile_registry = profile_registry or AgentProfileRegistry()
        self.step_executor = step_executor
        self.hooks = hook_manager or HookManager(store=store, enabled=False)
        self.review_parser = GateReviewParser()
        self.failure_router = FailureRouter()

    def run_ready_team_gates(self, run: RunRecord, contract: ContractSpec, limit: Optional[int] = None) -> List[GateRunResult]:
        results: List[GateRunResult] = []
        scope_by_id = contract.scope_by_id()
        for gate in contract.team_gates:
            if limit is not None and len(results) >= limit:
                break
            record = self.store.get_gate(run.id, f"team:{gate.scope_id}")
            if record is None or record.status in {"PASSED", "RUNNING"}:
                continue
            scope_items = [item for item in self.store.list_work_items(run.id) if item.scope_id == gate.scope_id]
            relevant = [
                item
                for item in scope_items
                if not item.id.startswith(("interface:", "scaffold:"))
            ]
            if not relevant:
                if scope_items and not all(item.status == "VERIFIED" for item in scope_items):
                    continue
                if not self._team_gate_can_run_without_implementation(run.id, contract, gate):
                    continue
            elif not all(item.status == "VERIFIED" for item in relevant):
                continue
            scope = scope_by_id.get(gate.scope_id)
            if scope is None:
                continue
            if not self._gate_dependencies_passed(run.id, gate):
                continue
            results.append(self._run_team_gate(run, contract, gate, scope_items))
        return results

    def _gate_dependencies_passed(self, run_id: str, gate: TeamGateSpec) -> bool:
        dependencies = [
            str(scope).strip()
            for scope in gate.test_plan.get("dependency_scope_ids", [])
            if str(scope).strip() and str(scope).strip() != gate.scope_id
        ]
        for scope_id in dependencies:
            record = self.store.get_gate(run_id, f"team:{scope_id}")
            if record is None or record.status != "PASSED":
                return False
        return True

    def run_ready_phase_gates(
        self,
        run: RunRecord,
        contract: ContractSpec,
        limit: Optional[int] = None,
    ) -> List[GateRunResult]:
        """Pass lightweight phase gates in order when their phase work is done.

        Phase gates are control-plane barriers. They do not replace team/final
        deterministic gates; they stop later phase work from activating before
        the current phase has produced its handoff artifacts.
        """

        results: List[GateRunResult] = []
        runtime_items = {item.id: item for item in self.store.list_work_items(run.id)}
        for phase in contract.phase_plan:
            if limit is not None and len(results) >= limit:
                break
            gate_id = f"phase:{phase.phase_id}"
            record = self.store.get_gate(run.id, gate_id)
            if record is None or record.status in {"PASSED", "RUNNING"}:
                continue
            phase_items = [
                runtime_items.get(item.id, item)
                for item in contract.work_items
                if str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")) == phase.phase_id
            ]
            if phase_items and not all(item.status == "VERIFIED" for item in phase_items):
                break
            blocker = self._phase_gate_blocker(run, contract, phase)
            if blocker:
                self.store.append_event(
                    run.id,
                    "phase_gate_waiting",
                    {"gate_id": gate_id, "phase_id": phase.phase_id, "reason": blocker},
                )
                break
            self.store.update_gate_status(run.id, gate_id, "RUNNING")
            missing = [
                artifact
                for artifact in (phase.handoff.artifacts if hasattr(phase.handoff, "artifacts") else [])
                if artifact
                and "*" not in artifact
                and artifact
                not in {
                    ".contractcoding/contract.json",
                    ".contractcoding/contract.md",
                    ".contractcoding/prd.md",
                }
                and not os.path.exists(os.path.join(run.workspace_dir or self.config.WORKSPACE_DIR, artifact))
            ]
            if missing:
                self.store.update_gate_status(
                    run.id,
                    gate_id,
                    "BLOCKED",
                    evidence=[f"Phase handoff artifact missing: {', '.join(missing)}"],
                    metadata={"missing_handoff_artifacts": missing},
                )
                results.append(
                    GateRunResult(
                        gate_id,
                        ran=True,
                        ok=False,
                        error=f"Phase handoff artifact missing: {', '.join(missing)}",
                    )
                )
                break
            evidence = [
                f"Phase `{phase.phase_id}` passed.",
                f"Mode: {phase.mode}.",
            ]
            if phase.deliverables:
                evidence.append(f"Deliverables: {', '.join(phase.deliverables[:12])}.")
            self.store.update_gate_status(
                run.id,
                gate_id,
                "PASSED",
                evidence=evidence,
                metadata={"phase": phase.to_record()},
            )
            self.hooks.emit(
                "after_gate",
                run_id=run.id,
                task_id=str(run.metadata.get("task_id", "")),
                payload={"gate_id": gate_id, "scope_id": "phase", "ok": True, "evidence": evidence},
            )
            results.append(GateRunResult(gate_id, ran=True, ok=True))
            break
        return results

    def _team_gate_can_run_without_implementation(
        self,
        run_id: str,
        contract: ContractSpec,
        gate: TeamGateSpec,
    ) -> bool:
        """Allow gate-only scopes such as generated planner/test scopes to close.

        Some large-project plans contain a scope whose implementation work is
        represented by frozen interface/scaffold items and gate-owned tests. If
        these gates never run, dependent interface/final gates can deadlock even
        though all WorkItems are VERIFIED.
        """

        if gate.scope_id == "tests":
            return self._all_non_test_team_gates_passed(run_id, contract, gate.scope_id)
        return bool(gate.test_artifacts)

    def _phase_gate_blocker(self, run: RunRecord, contract: ContractSpec, phase) -> str:
        checks = {str(value).strip().lower() for value in getattr(phase.phase_gate, "checks", [])}
        phase_id = str(phase.phase_id or "")
        requires_team_gates = (
            "team_gate" in checks
            or "team_gates" in checks
            or phase_id in {"hardening", "final_acceptance"}
        )
        requires_promotion = "promotion_readiness" in checks or phase_id in {"hardening", "final_acceptance"}
        scopes = list(phase.teams_in_scope) or [gate.scope_id for gate in contract.team_gates]
        scopes = [scope for scope in scopes if scope != "integration"]

        if requires_team_gates:
            pending = []
            for scope_id in scopes:
                gate = self.store.get_gate(run.id, f"team:{scope_id}")
                if gate is None or gate.status != "PASSED":
                    pending.append(scope_id)
            if pending:
                return "waiting for team gates: " + ", ".join(sorted(set(pending)))

        if requires_promotion:
            team_by_scope = {
                team.scope_id: team
                for team in self.store.list_scope_team_runs(run.id, limit=200)
                if team.scope_id != "integration"
            }
            pending = [
                scope_id
                for scope_id in scopes
                if (team_by_scope.get(scope_id) is not None and team_by_scope[scope_id].status not in {"PROMOTED", "CLOSED"})
            ]
            if pending:
                return "waiting for team promotion: " + ", ".join(sorted(set(pending)))

        return ""

    def _all_non_test_team_gates_passed(self, run_id: str, contract: ContractSpec, current_scope: str) -> bool:
        for gate in contract.team_gates:
            if gate.scope_id == current_scope or gate.scope_id == "tests":
                continue
            record = self.store.get_gate(run_id, f"team:{gate.scope_id}")
            if record is None or record.status != "PASSED":
                return False
        return True

    def run_final_gate_if_ready(self, run: RunRecord, contract: ContractSpec) -> GateRunResult:
        if contract.final_gate is None:
            return GateRunResult("final", ran=False)
        record = self.store.get_gate(run.id, "final")
        if record is None or record.status in {"PASSED", "RUNNING"}:
            return GateRunResult("final", ran=False)
        final_repair_scopes = self._runtime_final_repair_scopes(contract)
        team_gates = [
            self.store.get_gate(run.id, f"team:{gate.scope_id}")
            for gate in contract.team_gates
            if gate.scope_id not in final_repair_scopes
        ]
        if any(gate is None or gate.status != "PASSED" for gate in team_gates):
            return GateRunResult("final", ran=False)
        runtime_items = {item.id: item for item in self.store.list_work_items(run.id)}
        for item in contract.work_items:
            runtime_item = runtime_items.get(item.id)
            status = runtime_item.status if runtime_item is not None else item.status
            if status != "VERIFIED":
                return GateRunResult("final", ran=False)
        teams = self.store.list_scope_team_runs(run.id, limit=200)
        promotable = [team for team in teams if team.scope_id not in {"integration"}]
        if any(team.status not in {"PROMOTED", "CLOSED"} for team in promotable):
            return GateRunResult("final", ran=False)
        return self._run_final_gate(run, contract)

    @staticmethod
    def _runtime_final_repair_scopes(contract: ContractSpec) -> set[str]:
        values = {"final_repair"}
        values.update(
            str(value).strip()
            for value in (contract.metadata or {}).get("runtime_final_repair_scopes", [])
            if str(value).strip()
        )
        if (contract.metadata or {}).get("final_repair_mode") == "centralized_convergence":
            values.add("final_repair")
        return values

    def _run_team_gate(
        self,
        run: RunRecord,
        contract: ContractSpec,
        gate: TeamGateSpec,
        scope_items: List[WorkItem],
    ) -> GateRunResult:
        gate_id = f"team:{gate.scope_id}"
        workspace = self.team_runtime.ensure_workspace(run, contract, gate.scope_id)
        self.store.update_gate_status(run.id, gate_id, "RUNNING")
        self.hooks.emit(
            "before_team_dispatch",
            run_id=run.id,
            task_id=str(run.metadata.get("task_id", "")),
            payload={"scope_id": gate.scope_id, "gate_id": gate_id, "items": ["team_gate"]},
        )
        try:
            if gate.test_artifacts and self._team_gate_runs_scope_tests(gate):
                test_result = self._generate_gate_tests(run, contract, gate, scope_items, workspace)
                if not test_result.ok:
                    diagnostics = self._diagnostics_for_gate_failure(
                        gate_id=gate_id,
                        scope_id=gate.scope_id,
                        errors=[test_result.error],
                        affected_artifacts=gate.test_artifacts,
                    )
                    route = self.failure_router.classify_diagnostics(diagnostics)
                    self.store.update_gate_status(
                        run.id,
                        gate_id,
                        "FAILED",
                        evidence=[test_result.error],
                        metadata={
                            "failure_route": route.__dict__,
                            "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                        },
                    )
                    self._emit_diagnostics(run, diagnostics)
                    return test_result
            checker = GateChecker(workspace)
            started = time.perf_counter()
            scope = contract.scope_by_id().get(gate.scope_id)
            if scope is None:
                raise ValueError(f"Unknown gate scope: {gate.scope_id}")
            deterministic = checker.check_team_gate(
                contract=contract,
                scope=scope,
                gate=gate,
                scope_items=scope_items,
            )
            timing = {"team_gate_seconds": round(time.perf_counter() - started, 4)}
            if deterministic.errors:
                diagnostics = self._diagnostics_for_gate_failure(
                    gate_id=gate_id,
                    scope_id=gate.scope_id,
                    errors=deterministic.errors,
                    affected_artifacts=[
                        artifact
                        for item in scope_items
                        for artifact in item.target_artifacts
                    ],
                )
                route = self.failure_router.classify_diagnostics(diagnostics)
                self.store.update_gate_status(
                    run.id,
                    gate_id,
                    "FAILED",
                    evidence=deterministic.errors,
                    metadata={
                        "failure_route": route.__dict__,
                        "timing": timing,
                        "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                    },
                )
                self._emit_diagnostics(run, diagnostics)
                self._mark_team_blocked(run.id, gate.scope_id, "; ".join(deterministic.errors))
                return GateRunResult(gate_id, ran=True, ok=False, error="; ".join(deterministic.errors))
            review = self._run_gate_review(run, gate_id, "TeamReviewer", gate.review_policy, workspace, deterministic.evidence)
            if not review.ok:
                self.store.update_gate_status(run.id, gate_id, "BLOCKED", evidence=[review.error], metadata={"timing": timing})
                self._mark_team_blocked(run.id, gate.scope_id, review.error)
                return review
            self.store.update_gate_status(
                run.id,
                gate_id,
                "PASSED",
                evidence=deterministic.evidence,
                metadata={"timing": timing},
            )
            self.hooks.emit(
                "after_gate",
                run_id=run.id,
                task_id=str(run.metadata.get("task_id", "")),
                payload={"gate_id": gate_id, "scope_id": gate.scope_id, "ok": True, "evidence": deterministic.evidence},
            )
            return GateRunResult(gate_id, ran=True, ok=True)
        except Exception as exc:
            self.store.update_gate_status(run.id, gate_id, "BLOCKED", evidence=[str(exc)])
            self._mark_team_blocked(run.id, gate.scope_id, str(exc))
            return GateRunResult(gate_id, ran=True, ok=False, error=str(exc))

    def _run_final_gate(self, run: RunRecord, contract: ContractSpec) -> GateRunResult:
        gate_id = "final"
        self.store.update_gate_status(run.id, gate_id, "RUNNING")
        try:
            test_result = self._generate_final_gate_tests(run, contract)
            if not test_result.ok:
                diagnostics = self._diagnostics_for_final_gate_failure(
                    contract=contract,
                    errors=[test_result.error],
                    affected_artifacts=[
                        *self._final_test_artifacts(contract),
                        *(contract.final_gate.required_artifacts if contract.final_gate else []),
                    ],
                )
                route = self.failure_router.classify_diagnostics(diagnostics)
                self.store.update_gate_status(
                    run.id,
                    gate_id,
                    "FAILED",
                    evidence=[test_result.error],
                    metadata={
                        "failure_route": route.__dict__,
                        "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                    },
                )
                self._emit_diagnostics(run, diagnostics)
                return test_result
            checker = GateChecker(run.workspace_dir or self.config.WORKSPACE_DIR)
            started = time.perf_counter()
            deterministic = checker.check_final_gate(contract.final_gate)
            timing = {"final_gate_seconds": round(time.perf_counter() - started, 4)}
            if deterministic.errors:
                diagnostics = self._diagnostics_for_final_gate_failure(
                    contract=contract,
                    errors=deterministic.errors,
                    affected_artifacts=contract.final_gate.required_artifacts if contract.final_gate else [],
                )
                route = self.failure_router.classify_diagnostics(diagnostics)
                self.store.update_gate_status(
                    run.id,
                    gate_id,
                    "FAILED",
                    evidence=deterministic.errors,
                    metadata={
                        "failure_route": route.__dict__,
                        "timing": timing,
                        "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                    },
                )
                self._emit_diagnostics(run, diagnostics)
                return GateRunResult(gate_id, ran=True, ok=False, error="; ".join(deterministic.errors))
            review = self._run_gate_review(
                run,
                gate_id,
                "FinalReviewer",
                contract.final_gate.review_policy,
                run.workspace_dir or self.config.WORKSPACE_DIR,
                deterministic.evidence,
            )
            if not review.ok:
                self.store.update_gate_status(run.id, gate_id, "BLOCKED", evidence=[review.error], metadata={"timing": timing})
                return review
            self.store.update_gate_status(run.id, gate_id, "PASSED", evidence=deterministic.evidence, metadata={"timing": timing})
            self.hooks.emit(
                "after_gate",
                run_id=run.id,
                task_id=str(run.metadata.get("task_id", "")),
                payload={"gate_id": gate_id, "scope_id": "integration", "ok": True, "evidence": deterministic.evidence},
            )
            return GateRunResult(gate_id, ran=True, ok=True)
        except Exception as exc:
            self.store.update_gate_status(run.id, gate_id, "BLOCKED", evidence=[str(exc)])
            return GateRunResult(gate_id, ran=True, ok=False, error=str(exc))

    def _generate_final_gate_tests(self, run: RunRecord, contract: ContractSpec) -> GateRunResult:
        gate_record = self.store.get_gate(run.id, "final")
        metadata = gate_record.metadata if gate_record else {}
        targeted_repair_tests = [
            str(artifact).replace("\\", "/").strip("/")
            for artifact in metadata.get("target_test_artifacts", [])
            if str(artifact).strip()
        ]
        allow_test_repair = bool(metadata.get("allow_test_repair"))
        test_artifacts = (
            targeted_repair_tests
            if targeted_repair_tests and allow_test_repair
            else self._final_test_artifacts(contract)
        )
        if not test_artifacts:
            return GateRunResult("final", ran=False, ok=True)
        workspace = run.workspace_dir or self.config.WORKSPACE_DIR
        missing = [
            artifact
            for artifact in test_artifacts
            if not os.path.exists(os.path.join(workspace, artifact))
        ]
        target_tests = list(targeted_repair_tests if targeted_repair_tests and allow_test_repair else (test_artifacts if allow_test_repair else missing))
        prompt_mode = "repair invalid final integration tests" if allow_test_repair else "write missing final integration tests"
        if not target_tests:
            return GateRunResult("final", ran=False, ok=True)
        pseudo = WorkItem(
            id="gate:final:tests",
            kind="coding",
            title="Repair final integration gate tests" if allow_test_repair else "Generate final integration gate tests",
            owner_profile="Test_Engineer",
            scope_id="integration",
            target_artifacts=list(target_tests),
            acceptance_criteria=[
                "Tests import real generated modules and cover cross-scope behavior.",
                "Tests exercise integration scenarios required by the final gate.",
                "Tests contain executable unittest assertions and are not mock-only or all-skip.",
            ],
            conflict_keys=[f"artifact:{artifact}" for artifact in target_tests],
            team_role_hint="test_worker",
        )
        state = GeneralState(
            task=run.task,
            sub_task=self._final_test_generation_prompt(contract, target_tests, prompt_mode=prompt_mode),
            role="user",
            thinking="",
            output="",
        )
        step_id = self.store.create_step(
            run.id,
            pseudo.id,
            "Test_Engineer",
            {
                "final_gate": contract.final_gate.to_record() if contract.final_gate else {},
                "target_test_artifacts": list(target_tests),
                "allow_test_repair": allow_test_repair,
            },
        )
        try:
            with workspace_scope(workspace):
                output = self.step_executor(pseudo, "Test_Engineer", state) if self.step_executor else self._run_agent("Test_Engineer", state)
            payload = TeamExecutor._output_to_payload(output)
            self._record_llm_observability(run, pseudo, payload)
            payload.setdefault("wave_allowed_artifacts", list(target_tests))
            checker = SelfChecker(workspace)
            check = checker.check_item(pseudo, payload)
            if check.evidence:
                payload["system_validation"] = check.evidence
            if check.errors:
                infra_error = TeamExecutor._payload_infra_error(payload)
                if infra_error:
                    check.errors = [infra_error, *check.errors]
                error = "; ".join(check.errors)
                payload["validation_errors"] = check.errors
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=error)
                return GateRunResult("final", ran=True, ok=False, error=error)
            self.store.finish_step(step_id, "COMPLETED", output_payload=payload)
            if allow_test_repair:
                self.store.update_gate_status(
                    run.id,
                    "final",
                    "RUNNING",
                    metadata={"allow_test_repair": False, "target_test_artifacts": []},
                )
            return GateRunResult("final", ran=True, ok=True)
        except Exception as exc:
            self.store.finish_step(step_id, "ERROR", error=str(exc))
            return GateRunResult("final", ran=True, ok=False, error=str(exc))

    def _generate_gate_tests(
        self,
        run: RunRecord,
        contract: ContractSpec,
        gate: TeamGateSpec,
        scope_items: List[WorkItem],
        workspace: str,
    ) -> GateRunResult:
        gate_id = f"team:{gate.scope_id}"
        gate_record = self.store.get_gate(run.id, gate_id)
        allow_test_repair = bool((gate_record.metadata if gate_record else {}).get("allow_test_repair"))
        missing = [
            artifact
            for artifact in gate.test_artifacts
            if not os.path.exists(os.path.join(workspace, artifact))
        ]
        target_tests = list(gate.test_artifacts if allow_test_repair else missing)
        if not target_tests:
            return GateRunResult(gate_id, ran=False, ok=True)
        pseudo = WorkItem(
            id=f"gate:{gate.scope_id}:tests",
            kind="coding",
            title=(
                f"Repair {gate.scope_id} team gate tests"
                if allow_test_repair
                else f"Generate {gate.scope_id} team gate tests"
            ),
            owner_profile="Test_Engineer",
            scope_id=gate.scope_id,
            target_artifacts=list(target_tests),
            acceptance_criteria=[
                "Tests import real generated modules and contain executable assertions.",
                "Tests map to the team gate test plan and must not skip solely because an API guess is wrong.",
            ],
            conflict_keys=[f"artifact:{artifact}" for artifact in target_tests],
            team_role_hint="test_worker",
        )
        state = GeneralState(
            task=run.task,
            sub_task=self._test_generation_prompt(
                gate,
                scope_items,
                target_artifacts=target_tests,
                mode="repair invalid tests" if allow_test_repair else "write missing tests",
            ),
            role="user",
            thinking="",
            output="",
        )
        step_id = self.store.create_step(run.id, pseudo.id, "Test_Engineer", {"gate": gate.to_record()})
        try:
            with workspace_scope(workspace):
                output = self.step_executor(pseudo, "Test_Engineer", state) if self.step_executor else self._run_agent("Test_Engineer", state)
            payload = TeamExecutor._output_to_payload(output)
            self._record_llm_observability(run, pseudo, payload)
            payload.setdefault("wave_allowed_artifacts", list(target_tests))
            checker = SelfChecker(workspace)
            check = checker.check_item(pseudo, payload)
            if check.evidence:
                payload["system_validation"] = check.evidence
            if check.errors:
                infra_error = TeamExecutor._payload_infra_error(payload)
                if infra_error:
                    check.errors = [infra_error, *check.errors]
                error = "; ".join(check.errors)
                payload["validation_errors"] = check.errors
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=error)
                return GateRunResult(f"team:{gate.scope_id}", ran=True, ok=False, error=error)
            audit = TestStrataAuditor(workspace).audit_scope_tests(
                scope_id=gate.scope_id,
                test_artifacts=target_tests,
                scope_artifacts=[
                    artifact
                    for item in scope_items
                    for artifact in item.target_artifacts
                ],
                dependency_scope_ids=gate.test_plan.get("dependency_scope_ids", []),
            )
            if audit.evidence:
                payload["test_strata_audit"] = audit.evidence
            if audit.errors:
                payload["validation_errors"] = audit.errors
                error = "\n".join(audit.errors)
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=error)
                return GateRunResult(f"team:{gate.scope_id}", ran=True, ok=False, error=error)
            self.store.finish_step(step_id, "COMPLETED", output_payload=payload)
            if allow_test_repair:
                self.store.update_gate_status(run.id, gate_id, "RUNNING", metadata={"allow_test_repair": False})
            return GateRunResult(f"team:{gate.scope_id}", ran=True, ok=True)
        except Exception as exc:
            self.store.finish_step(step_id, "ERROR", error=str(exc))
            return GateRunResult(f"team:{gate.scope_id}", ran=True, ok=False, error=str(exc))

    @staticmethod
    def _team_gate_runs_scope_tests(gate: TeamGateSpec) -> bool:
        checks = {str(value).strip().lower() for value in gate.deterministic_checks}
        return "scope_tests" in checks or "scope_test" in checks

    def _run_gate_review(
        self,
        run: RunRecord,
        gate_id: str,
        reviewer: str,
        review_policy: Dict[str, Any],
        workspace: str,
        evidence: Iterable[str],
    ) -> GateRunResult:
        if self.agent_runner is None and self.step_executor is None:
            return GateRunResult(gate_id, ran=False, ok=True)
        skill_context = self._gate_review_skill_context()
        sub_task = (
            f"Gate review: {gate_id}\n"
            f"Review policy: {review_policy}\n"
            "Deterministic gate evidence:\n"
            + "\n".join(f"- {item}" for item in evidence)
        )
        if skill_context:
            sub_task += "\n\nRelevant review skills:\n" + skill_context
        sub_task += (
            "\nReturn <gate_review>{\"verdict\":\"pass|pass_with_risks|fail|blocked\","
            "\"block_reason\":\"...\",\"evidence\":[],\"risks\":[]}</gate_review>."
        )
        state = GeneralState(
            task=run.task,
            sub_task=sub_task,
            role="user",
            thinking="",
            output="",
        )
        step_id = self.store.create_step(run.id, f"{gate_id}:review", reviewer, {"review_policy": review_policy})
        try:
            with workspace_scope(workspace):
                output = self._run_agent(reviewer, state)
            payload = TeamExecutor._output_to_payload(output)
            verdict = self.review_parser.parse(payload, review_policy)
            payload["gate_review"] = {
                "verdict": verdict.verdict,
                "block_reason": verdict.block_reason,
                "evidence": verdict.evidence,
                "risks": verdict.risks,
            }
            if not verdict.accepted:
                self.store.finish_step(step_id, "ERROR", output_payload=payload, error=verdict.error)
                return GateRunResult(gate_id, ran=True, ok=False, error=verdict.error)
            self.store.finish_step(step_id, "COMPLETED", output_payload=payload)
            return GateRunResult(gate_id, ran=True, ok=True)
        except Exception as exc:
            self.store.finish_step(step_id, "ERROR", error=str(exc))
            return GateRunResult(gate_id, ran=True, ok=False, error=str(exc))

    def _run_agent(self, agent_name: str, state: GeneralState) -> Any:
        if self.agent_runner is None:
            return GeneralState(task=state.task, sub_task=state.sub_task, role=agent_name, output="PASS")
        return self.agent_runner.run(
            agent_name=self.profile_registry.resolve_agent_name(agent_name, "eval"),
            state=state,
            next_available_agents=list(self.agent_runner.agents.keys()),
        )

    def _gate_review_skill_context(self) -> str:
        if self.context_manager is None:
            return ""
        selected = [
            skill
            for skill in self.context_manager.skills_for("eval")
            if skill.name in {"coding.review_gate_workflow", "coding.evaluator_gate", "coding.final_recovery"}
        ]
        return ContextManager._fit_text("\n\n".join(skill.render() for skill in selected), 5000)

    def _record_llm_observability(self, run: RunRecord, item: WorkItem, payload: Dict[str, Any]) -> None:
        observed = payload_observability(payload)
        if (
            not observed
            or not any(
                int(observed.get(key, 0) or 0)
                for key in (
                    "event_count",
                    "tool_intent_count",
                    "tool_result_count",
                    "prompt_tokens",
                    "completion_tokens",
                    "timeout_count",
                    "empty_response_count",
                    "attempt_count",
                )
            )
            and not observed.get("infra_failure")
        ):
            return
        summary = {
            "work_item_id": item.id,
            "backend": observed.get("backend", "unknown"),
            "event_count": int(observed.get("event_count", 0) or 0),
            "timeout_count": int(observed.get("timeout_count", 0) or 0),
            "empty_response_count": int(observed.get("empty_response_count", 0) or 0),
            "tool_intent_count": int(observed.get("tool_intent_count", 0) or 0),
            "tool_result_count": int(observed.get("tool_result_count", 0) or 0),
            "prompt_tokens": int(observed.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(observed.get("completion_tokens", 0) or 0),
            "attempt_count": int(observed.get("attempt_count", 0) or 0),
            "infra_failure": bool(observed.get("infra_failure")),
            "failure_kind": observed.get("failure_kind") or "",
            "returncode": observed.get("returncode"),
            "last_event": observed.get("last_event", {}),
            "stop_reason": observed.get("stop_reason") or "",
            "terminal_tool": observed.get("terminal_tool") or "",
            "tool_iterations": int(observed.get("tool_iterations", 0) or 0),
        }
        payload["llm_observability"] = summary
        self.store.append_event(run.id, "llm_observed", summary)

    @staticmethod
    def _test_generation_prompt(
        gate: TeamGateSpec,
        scope_items: List[WorkItem],
        *,
        target_artifacts: Optional[List[str]] = None,
        mode: str = "write missing tests",
    ) -> str:
        targets = list(target_artifacts or gate.test_artifacts)
        target_files = "\n".join(f"- {artifact}" for artifact in targets) or "- None"
        implementation_files = "\n".join(
            f"- {artifact}"
            for item in scope_items
            for artifact in item.target_artifacts
        ) or "- None"
        return (
            f"Module team: {gate.scope_id}\n"
            "Owner packet: Test_Engineer\n"
            "Execution plane: sandbox\n"
            "Wave allowed artifacts:\n"
            f"{target_files}\n\n"
            f"Task: {mode} for the team gate.\n"
            "Target files in this module wave:\n"
            f"{target_files}\n\n"
            "Implementation artifacts to test:\n"
            f"{implementation_files}\n\n"
            f"Team gate test plan: {gate.test_plan}\n"
            "Use Python unittest. This is a scope-local gate, not final integration. Test only this scope's public "
            "behavior declared by the contract, team interface, or real module APIs you inspected. Do not import or "
            "mock.patch dependency-scope modules that are not present in this workspace; patch through functions or "
            "classes in the current scope module, or return a structured blocker if the behavior requires a promoted "
            "dependency scope. Avoid brittle assertions about private ordering, "
            "internal constants, or guessed semantics. Do not write mock-only tests, empty tests, or all-skip tests. "
            "Do not replace, delete, or fake the generated package root in sys.modules; tests must import the real "
            "package and may only mock external IO, stdin/stdout, clocks, randomness, or temporary files. "
            "When using TemporaryDirectory or other temporary-file contexts, assert path existence and file contents "
            "inside the context before cleanup; never assert that a cleaned-up temporary path still exists. "
            "Existing tests are locked after valid generation; only edit an existing test when this packet explicitly "
            "says repair invalid tests. If a normal assertion fails, do not change tests; implementation repair will "
            "be routed by ContractCoding. Only return a structured blocker for an out-of-scope artifact or a genuinely "
            "ambiguous/missing public API. "
            "Do not request run_code; ContractCoding runs deterministic gate verification after this step."
        )

    def _final_test_artifacts(self, contract: ContractSpec) -> List[str]:
        if contract.final_gate is None:
            return []
        return [
            artifact
            for artifact in contract.final_gate.required_artifacts
            if self._is_test_artifact(artifact)
        ]

    def _final_test_generation_prompt(
        self,
        contract: ContractSpec,
        test_artifacts: List[str],
        *,
        prompt_mode: str = "write missing final integration tests",
    ) -> str:
        target_files = "\n".join(f"- {artifact}" for artifact in test_artifacts) or "- None"
        implementation_files = "\n".join(
            f"- {artifact}"
            for artifact in (contract.final_gate.required_artifacts if contract.final_gate else [])
            if not self._is_test_artifact(artifact)
        ) or "- None"
        acceptance = "\n".join(
            f"- {scenario}"
            for scenario in (contract.final_gate.final_acceptance_scenarios if contract.final_gate else [])
        ) or "- None"
        product_behavior = contract.final_gate.product_behavior if contract.final_gate else {}
        behavior_lines: List[str] = []
        if product_behavior:
            capabilities = product_behavior.get("capabilities", [])
            if capabilities:
                behavior_lines.append("Capabilities: " + ", ".join(str(item) for item in capabilities))
            commands = product_behavior.get("blackbox_commands", [])
            if commands:
                behavior_lines.append("Blackbox commands that final tests should exercise through subprocess when feasible:")
                for command in commands:
                    if isinstance(command, dict):
                        argv = " ".join(str(part) for part in command.get("argv", []))
                        behavior_lines.append(f"- {command.get('id', 'command')}: {argv}")
            requirements = product_behavior.get("semantic_requirements", [])
            if requirements:
                behavior_lines.append("Anti-shallow semantic requirements:")
                for requirement in requirements:
                    if isinstance(requirement, dict):
                        behavior_lines.append(
                            f"- {requirement.get('id', 'semantic')}: "
                            f"{requirement.get('description', '')}"
                        )
        behavior_context = "\n".join(behavior_lines) or "- None"
        return (
            "Final integration gate\n"
            "Owner packet: Test_Engineer\n"
            "Execution plane: main workspace\n"
            "Wave allowed artifacts:\n"
            f"{target_files}\n\n"
            f"Task: {prompt_mode}.\n"
            "Target files in this module wave:\n"
            f"{target_files}\n\n"
            "Generated package artifacts to test:\n"
            f"{implementation_files}\n\n"
            "Final acceptance scenarios from the contract:\n"
            f"{acceptance}\n\n"
            "Product behavior contract:\n"
            f"{behavior_context}\n\n"
            "Cover cross-scope behavior: domain state, core simulation, AI decisions, IO save/load, CLI smoke, "
            "and a complete multi-turn scenario. Use Python unittest. Import real generated modules and assert "
            "public contract behavior rather than private implementation details. "
            "For CLI or command-line products, include at least one subprocess-based blackbox test using "
            "`sys.executable -m <public module>`; direct calls such as cli.main([...]) are useful but do not prove "
            "the module entrypoint works. "
            "For simulation/planning products, assert semantic event/state facts, not just that strings are printed: "
            "failure/repair capabilities must produce failure/repair events, route optimization must feed planning, "
            "reports must agree with structured summaries, and save/load/replay must preserve state. "
            "Hard target completion: create or update every file listed under Target files in this module wave. "
            "Do not merely repair existing tests when the target list contains missing files. "
            "If the target files are scope-specific tests such as tests/test_domain.py or tests/test_engine.py, "
            "write focused executable tests for that scope's public APIs while still using only real package imports. "
            "Do not write mock-only tests, empty tests, or all-skip tests. Do not replace, delete, or fake the "
            "generated package root in sys.modules; use real package imports and mock only external IO/stdin/stdout/"
            "temporary files. Do not invent private APIs, default scenario names, or command semantics that are not "
            "present in the contract acceptance scenarios or real public modules you inspected. "
            "When using TemporaryDirectory or other temporary-file contexts, assert path existence and file contents "
            "inside the context before cleanup; never assert that a cleaned-up temporary path still exists. "
            "Existing integration tests are locked after valid generation; only edit an existing test when this packet "
            "explicitly says repair invalid final integration tests. If a normal assertion fails, do not change tests; "
            "implementation repair will be routed by ContractCoding. Only return a structured blocker for an "
            "out-of-scope artifact or a genuinely ambiguous/missing public API. If a real public command or import "
            "fails because production code has an undefined helper/export/runtime bug, report an implementation_bug "
            "blocker naming the suspected artifact and symbol; do not weaken tests to hide the bug. "
            "Do not request run_code; "
            "ContractCoding will run deterministic final verification after this step."
        )

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    def _mark_team_blocked(self, run_id: str, scope_id: str, error: str) -> None:
        team = self.store.get_scope_team_run(run_id, scope_id)
        if team is not None:
            self.store.update_team_run_status(team.id, "BLOCKED", {"gate_error": error})

    @staticmethod
    def _diagnostics_for_gate_failure(
        *,
        gate_id: str,
        scope_id: str,
        errors: Iterable[str],
        affected_artifacts: Iterable[str],
    ) -> List[DiagnosticRecord]:
        return DiagnosticBuilder.from_gate_failure(
            gate_id=gate_id,
            scope_id=scope_id,
            errors=errors,
            affected_artifacts=affected_artifacts,
        )

    @staticmethod
    def _diagnostics_for_final_gate_failure(
        *,
        contract: ContractSpec,
        errors: Iterable[str],
        affected_artifacts: Iterable[str],
    ) -> List[DiagnosticRecord]:
        required = list(affected_artifacts)
        if contract.final_gate is not None and not required:
            required = list(contract.final_gate.required_artifacts)
        return DiagnosticBuilder.from_final_gate_failure(
            errors=errors,
            required_artifacts=required,
            artifact_scope_map=GateRunner._artifact_scope_map(contract),
        )

    @staticmethod
    def _artifact_scope_map(contract: ContractSpec) -> Dict[str, str]:
        out: Dict[str, str] = {
            str(path).replace("\\", "/"): str(scope)
            for path, scope in dict(getattr(contract, "owner_hints", {}) or {}).items()
            if str(path).strip() and str(scope).strip()
        }
        for scope in contract.work_scopes:
            for artifact in scope.artifacts:
                out.setdefault(str(artifact).replace("\\", "/"), scope.id)
        for item in contract.work_items:
            for artifact in item.target_artifacts:
                normalized = str(artifact).replace("\\", "/")
                if normalized.startswith(".contractcoding/interfaces/"):
                    continue
                out.setdefault(normalized, item.scope_id)
        return out

    def _emit_diagnostics(self, run: RunRecord, diagnostics: List[DiagnosticRecord]) -> None:
        for diagnostic in diagnostics:
            self.store.append_event(
                run.id,
                "diagnostic_recorded",
                {
                    "gate_id": diagnostic.gate_id,
                    "scope_id": diagnostic.scope_id,
                    "failure_kind": diagnostic.failure_kind,
                    "fingerprint": diagnostic.fingerprint(),
                    "summary": diagnostic.summary(),
                    "test_artifacts": diagnostic.test_artifacts,
                    "suspected_implementation_artifacts": diagnostic.suspected_implementation_artifacts,
                    "suspected_scopes": diagnostic.suspected_scopes,
                    "external_artifacts": diagnostic.external_artifacts,
                },
            )
