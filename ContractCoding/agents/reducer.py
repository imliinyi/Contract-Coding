"""Deterministic reducer for typed contract operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from ..contract.kernel import TeamContract
from ..contract.operation import ContractOperation, OperationKind, OperationStatus
from ..contract.work import WorkStatus
from ..registry import RegistryTool
from .auditor import ContractAuditor, split_capsule_ref


@dataclass
class ReducerResult:
    operation: ContractOperation
    accepted: bool
    reasons: List[str]


@dataclass
class ContractReducer:
    tool: RegistryTool
    auditor: ContractAuditor

    def process(self, operation: ContractOperation) -> ReducerResult:
        errors = self.auditor.audit_operation(operation)
        if errors:
            operation.reject(errors)
            self.tool.append_contract_operation(operation)
            return ReducerResult(operation=operation, accepted=False, reasons=errors)

        operation.accept()
        self._apply(operation)
        self.tool.append_contract_operation(operation)
        return ReducerResult(operation=operation, accepted=True, reasons=[])

    def apply_pending(self, operations: Iterable[ContractOperation]) -> List[ReducerResult]:
        ops = list(operations)
        decided = {
            op.op_id
            for op in ops
            if op.status in (OperationStatus.ACCEPTED, OperationStatus.REJECTED, OperationStatus.SUPERSEDED)
        }
        results: List[ReducerResult] = []
        for op in ops:
            if op.status == OperationStatus.PROPOSED and op.op_id not in decided:
                results.append(self.process(op))
        return results

    # ------------------------------------------------------------------ apply

    def _apply(self, op: ContractOperation) -> None:
        if op.kind == OperationKind.DECLARE_API:
            self._apply_declare_api(op)
        elif op.kind == OperationKind.DECLARE_DEPENDENCY:
            self._apply_declare_dependency(op)
        elif op.kind == OperationKind.SUBMIT_EVIDENCE:
            self._apply_submit_evidence(op)
        elif op.kind == OperationKind.REPORT_BLOCKER:
            self._apply_report_blocker(op)
        elif op.kind == OperationKind.RECORD_DECISION:
            self._apply_record_decision(op)

    def _team(self, team_id: str) -> TeamContract:
        return self.tool.get_team_contract(team_id) or TeamContract.empty(team_id)

    def _apply_declare_api(self, op: ContractOperation) -> None:
        team = self._team(op.from_team)
        capability = str(op.payload.get("capability") or op.target_ref.split("/")[-1])
        team.public_apis[capability] = {
            "target_ref": op.target_ref,
            "symbols": list(op.payload.get("symbols", []) or []),
            "files": list(op.payload.get("files", []) or []),
            "interface_def": dict(op.payload.get("interface_def", {}) or {}),
            "evidence_refs": list(op.evidence_refs),
            "op_id": op.op_id,
        }
        self.tool.write_team_contract(team)

    def _apply_declare_dependency(self, op: ContractOperation) -> None:
        team = self._team(op.from_team)
        owner, capability = split_capsule_ref(op.target_ref)
        if not owner:
            owner = op.target_team
        rendered = f"{owner}/{capability}"
        team.dependencies.setdefault(owner, [])
        if capability not in team.dependencies[owner]:
            team.dependencies[owner].append(capability)
        self.tool.write_team_contract(team)
        if owner and capability:
            self.tool.add_consumer(owner, capability, op.from_team)

    def _apply_submit_evidence(self, op: ContractOperation) -> None:
        team = self._team(op.from_team)
        target_work = op.target_ref.replace("work:", "", 1)
        for item in team.work_items:
            if item.work_id == target_work or item.task_id in op.related_task_ids:
                if item.status != WorkStatus.DONE:
                    item.status = WorkStatus.DONE
                if op.evidence_refs:
                    for ref in op.evidence_refs:
                        if ref.startswith("validation:") and ref not in item.validation_commands:
                            item.validation_commands.append(ref)
        self.tool.write_team_contract(team)

    def _apply_report_blocker(self, op: ContractOperation) -> None:
        team = self._team(op.from_team)
        target_work = op.target_ref.replace("work:", "", 1)
        for item in team.work_items:
            if item.work_id == target_work or item.task_id in op.related_task_ids:
                item.status = WorkStatus.BLOCKED
        self.tool.write_team_contract(team)

    def _apply_record_decision(self, op: ContractOperation) -> None:
        statement = str(op.payload.get("statement", ""))
        if not statement:
            return
        self.tool.append_decision(
            op.from_team,
            statement,
            rationale=str(op.payload.get("rationale", op.rationale)),
            evidence=op.evidence_refs,
        )
