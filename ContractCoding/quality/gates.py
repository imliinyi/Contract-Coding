"""Team and final deterministic gates."""

from __future__ import annotations

from typing import Iterable, List

from ContractCoding.contract.spec import ContractSpec, FinalGateSpec, TeamGateSpec, WorkScope
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.runtime.invariants import InvariantChecker, InvariantResult


class GateChecker(InvariantChecker):
    def check_team_gate(
        self,
        *,
        contract: ContractSpec,
        scope: WorkScope,
        gate: TeamGateSpec,
        scope_items: Iterable[WorkItem],
    ) -> InvariantResult:
        scope_artifacts = [
            artifact
            for item in scope_items
            if not item.id.startswith(("interface:", "scaffold:"))
            for artifact in item.target_artifacts
            if not self._is_python_test_artifact(artifact)
            and not artifact.startswith(".contractcoding/")
        ]
        python_artifacts = [
            artifact
            for artifact in [*scope_artifacts, *gate.test_artifacts]
            if artifact.endswith(".py")
        ]
        interface_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == scope.id and item.id.startswith("interface:")
            for artifact in item.target_artifacts
        ]
        item = WorkItem(
            id=f"gate:{scope.id}",
            kind="eval",
            scope_id=scope.id,
            target_artifacts=[f".contractcoding/scope_reports/{scope.id}.json"],
            inputs={
                "scope_id": scope.id,
                "scope_artifacts": scope_artifacts,
                "test_artifacts": list(gate.test_artifacts),
                "python_artifacts": python_artifacts,
                "interface_artifacts": interface_artifacts,
                "interface_specs": [
                    spec.to_record()
                    for spec in contract.interfaces_for_scope(scope.id)
                    if spec.status in {"FROZEN", "IMPLEMENTED", "VERIFIED"}
                ],
                "requires_tests": bool(gate.test_artifacts),
                "deterministic_checks": list(gate.deterministic_checks),
            },
            verification_policy={"system_gate": "scope"},
        )
        return self._check_scope_gate(item)

    def check_final_gate(self, final_gate: FinalGateSpec) -> InvariantResult:
        item = WorkItem(
            id="gate:final",
            kind="eval",
            scope_id="integration",
            target_artifacts=[".contractcoding/integration_report.json"],
            inputs={
                "required_artifacts": list(final_gate.required_artifacts),
                "python_artifacts": list(final_gate.python_artifacts),
                "package_roots": list(final_gate.package_roots),
                "requires_tests": bool(final_gate.requires_tests),
                "allowed_extra_paths": list(final_gate.allowed_extra_paths),
                "product_behavior": dict(final_gate.product_behavior),
            },
            verification_policy={"system_gate": "integration"},
        )
        return self._check_integration_gate(item)
