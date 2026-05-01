"""Run health diagnostics for long-running runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import List

from ContractCoding.runtime.store import RunRecord, RunStore
from ContractCoding.runtime.scheduler import Scheduler


FAILURE_INFRA = "infra"
FAILURE_ITEM_QUALITY = "item_quality"
FAILURE_CONTRACT_PLAN = "contract_plan"
FAILURE_HUMAN_REQUIRED = "human_required"


@dataclass
class HealthDiagnostic:
    severity: str
    code: str
    message: str
    replan_recommended: bool = False
    recovery_action: str = ""
    failure_kind: str = ""


@dataclass
class RunHealth:
    status: str
    diagnostics: List[HealthDiagnostic] = field(default_factory=list)

    @property
    def replan_recommended(self) -> bool:
        return any(item.replan_recommended for item in self.diagnostics)

    def to_record(self) -> dict:
        return {
            "status": self.status,
            "replan_recommended": self.replan_recommended,
            "diagnostics": [diagnostic.__dict__ for diagnostic in self.diagnostics],
        }


class HealthMonitor:
    def __init__(self, store: RunStore, scheduler: Scheduler):
        self.store = store
        self.scheduler = scheduler

    def check(self, run_id: str) -> RunHealth:
        run = self.store.get_run(run_id)
        if run is None:
            return RunHealth("FAILED", [HealthDiagnostic("error", "unknown_run", f"Unknown run {run_id}")])

        diagnostics: List[HealthDiagnostic] = []
        items = self.store.list_work_items(run_id)
        item_status_by_id = {item.id: item.status for item in items}
        steps = self.store.latest_steps(run_id, limit=20)
        team_runs = self.store.list_team_runs(run_id, limit=20)
        gates = self.store.list_gates(run_id)
        active_leases = self.store.active_leased_items(run_id)
        waves = self.scheduler.next_wave(run_id)

        for item in items:
            if item.status == "BLOCKED":
                latest = self.store.latest_step_for_item(run_id, item.id)
                failure_text = self._step_failure_text(latest) if latest else "; ".join(item.evidence)
                failure_kind = self._classify_failure(failure_text)
                recovery_action = {
                    FAILURE_INFRA: "infra_retry",
                    FAILURE_ITEM_QUALITY: "item_repair",
                    FAILURE_CONTRACT_PLAN: "contract_replan",
                    FAILURE_HUMAN_REQUIRED: "request_human",
                }.get(failure_kind, "item_repair")
                diagnostics.append(
                    HealthDiagnostic(
                        "warn",
                        "work_item_blocked",
                        f"{item.id} is BLOCKED ({failure_kind}).",
                        replan_recommended=failure_kind == FAILURE_CONTRACT_PLAN,
                        recovery_action=recovery_action,
                        failure_kind=failure_kind,
                    )
                )

        for gate in gates:
            if gate.status in {"FAILED", "BLOCKED"}:
                text = "\n".join(gate.evidence[-5:])
                failure_kind = self._classify_failure(text)
                diagnostics.append(
                    HealthDiagnostic(
                        "warn",
                        "gate_blocked",
                        f"{gate.gate_id} gate is {gate.status} ({failure_kind}).",
                        replan_recommended=failure_kind == FAILURE_CONTRACT_PLAN,
                        recovery_action={
                            FAILURE_INFRA: "infra_retry",
                            FAILURE_ITEM_QUALITY: "gate_repair",
                            FAILURE_CONTRACT_PLAN: "contract_replan",
                            FAILURE_HUMAN_REQUIRED: "request_human",
                        }.get(failure_kind, "gate_repair"),
                        failure_kind=failure_kind,
                    )
                )

        raw_error_steps = [step for step in steps if step.status == "ERROR"]
        unresolved_error_steps = [
            step
            for step in raw_error_steps
            if item_status_by_id.get(step.work_item_id) != "VERIFIED"
        ]
        if unresolved_error_steps and run.status != "COMPLETED":
            failure_kind = self._classify_failure(
                "\n".join(self._step_failure_text(step) for step in unresolved_error_steps)
            )
            diagnostics.append(
                HealthDiagnostic(
                    "warn",
                    "recent_step_error",
                    f"{len(unresolved_error_steps)} recent step(s) ended in ERROR ({failure_kind}).",
                    replan_recommended=failure_kind == FAILURE_CONTRACT_PLAN,
                    recovery_action={
                        FAILURE_INFRA: "infra_retry",
                        FAILURE_ITEM_QUALITY: "item_repair",
                        FAILURE_CONTRACT_PLAN: "contract_replan",
                    }.get(failure_kind, "request_human"),
                    failure_kind=failure_kind,
                )
            )

        has_running_step = any(step.status == "RUNNING" for step in steps)
        has_running_team = any(team_run.status == "RUNNING" for team_run in team_runs)
        if (
            run.status == "RUNNING"
            and not active_leases
            and not has_running_step
            and not has_running_team
            and not waves
            and not self._all_terminal(items, gates)
        ):
            diagnostics.append(
                HealthDiagnostic(
                    "warn",
                    "no_progress",
                    "Run is RUNNING but has no active leases or ready waves.",
                    replan_recommended=True,
                    recovery_action="diagnostic_replan",
                    failure_kind=FAILURE_CONTRACT_PLAN,
                )
            )

        if run.status == "BLOCKED":
            diagnostics.append(
                HealthDiagnostic(
                    "warn",
                    "run_blocked",
                    "Run status is BLOCKED.",
                    replan_recommended=not any(diagnostic.recovery_action in {"infra_retry", "item_repair"} for diagnostic in diagnostics),
                    recovery_action="diagnostic_replan",
                    failure_kind=FAILURE_CONTRACT_PLAN,
                )
            )

        if run.status == "FAILED":
            diagnostics.append(
                HealthDiagnostic(
                    "error",
                    "run_failed",
                    "Run status is FAILED.",
                    replan_recommended=False,
                    recovery_action="request_human",
                    failure_kind=FAILURE_HUMAN_REQUIRED,
                )
            )

        if any(diagnostic.severity == "error" for diagnostic in diagnostics):
            return RunHealth("FAILED", diagnostics)
        if run.status == "COMPLETED" and raw_error_steps:
            return RunHealth("OK_WITH_RECOVERED_EVENTS", diagnostics)
        if diagnostics:
            return RunHealth("WARN", diagnostics)
        return RunHealth("OK", diagnostics)

    @staticmethod
    def _all_terminal(items, gates=None) -> bool:
        gates = gates or []
        return bool(items) and all(item.status == "VERIFIED" for item in items) and all(
            gate.status == "PASSED" for gate in gates
        )

    @staticmethod
    def _classify_failure(text: str) -> str:
        lower = str(text or "").lower()
        infra_markers = (
            "runs.sqlite",
            "sqlite-journal",
            "sqlite-wal",
            "sqlite-shm",
            "sandbox",
            "worktree creation failed",
            "execution plane",
            "llm returned an empty",
            "llm infrastructure failure",
            "failed to create session",
            "attempt to write a readonly database",
            "operation not permitted",
            "failed to refresh available models",
            "stream disconnected before completion",
            "error sending request for url",
            "llm backend",
            "infra_failure",
            '"failure_kind": "infra"',
            "empty final message",
            "tool intent",
            "json parse",
            "timed out waiting for llm",
        )
        contract_markers = (
            "dependency cycle",
            "depends on unknown",
            "unknown scope",
            "interface missing",
            "declared stable interfaces",
            "contract validation",
        )
        human_markers = (
            "requires approval",
            "requires approved source access",
            "permission denied",
            "outside work-item artifact scope",
            "dangerous command",
            "provided source material",
            "source access unavailable",
            "source-gathering",
            "no sources were consulted",
        )
        source_human_markers = (
            "requires approved source access",
            "provided source material",
            "source access unavailable",
            "source-gathering",
            "no sources were consulted",
        )
        item_quality_markers = (
            "invalid blocker",
            "required artifacts are already allowed",
            "placeholder",
            "syntax validation failed",
            "import validation failed",
            "unit test validation failed",
            "integration gate failed",
            "scope gate",
            "target artifact missing",
            "required tests",
            "unittest discovery failed",
            "notimplemented",
            "not implemented",
        )
        blocker_markers = (
            "agent reported a blocker",
            "out_of_scope_repair",
            "out-of-scope repair",
            "outside this workitem",
            "outside this work item",
            "outside allowed artifacts",
        )
        if any(marker in lower for marker in source_human_markers):
            return FAILURE_HUMAN_REQUIRED
        if any(marker in lower for marker in item_quality_markers):
            return FAILURE_ITEM_QUALITY
        if any(marker in lower for marker in blocker_markers):
            return FAILURE_HUMAN_REQUIRED
        if any(marker in lower for marker in human_markers):
            return FAILURE_HUMAN_REQUIRED
        if any(marker in lower for marker in contract_markers):
            return FAILURE_CONTRACT_PLAN
        if any(marker in lower for marker in infra_markers):
            return FAILURE_INFRA
        return FAILURE_ITEM_QUALITY

    @staticmethod
    def _step_failure_text(step) -> str:
        if step is None:
            return ""
        try:
            output_text = json.dumps(step.output or {}, ensure_ascii=False)
        except TypeError:
            output_text = str(step.output or "")
        return "\n".join(part for part in (step.error, output_text) if part)
