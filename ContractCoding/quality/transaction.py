"""Unified quality transactions for tests plus review.

The runtime treats test and review as one auditable transaction. Tests provide
deterministic evidence; review decides whether that evidence is sufficient for
promotion without inventing new product semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Dict, List

from ContractCoding.contract.spec import (
    ContractSpec,
    FeatureSlice,
    QualityTransactionRecord,
    WorkItem,
    _dedupe,
)
from ContractCoding.quality.gates import CapsuleJudge, GateResult, IntegrationJudge, RepairJudge, SliceJudge


APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
NEED_MORE_TESTS = "NEED_MORE_TESTS"
SEMANTIC_REPLAN = "SEMANTIC_REPLAN"


@dataclass
class QualityReviewResult:
    verdict: str
    evidence: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == APPROVE


@dataclass
class QualityTransactionResult:
    ok: bool
    record: QualityTransactionRecord
    gate_result: GateResult
    test_result: GateResult
    review_result: QualityReviewResult


class QualityReviewJudge:
    """Deterministic reviewer over patch evidence and contract-aligned tests."""

    SEMANTIC_CODES = {
        "forbidden_value_object_equivalence",
        "ungrounded_acceptance_assertion",
        "ungrounded_numeric_acceptance_assertion",
        "semantic_kernel_conflict",
        "kernel_ontology_conflict",
    }

    EXECUTABLE_EVIDENCE_PREFIXES = (
        "compile:",
        "slice_smoke_",
        "repair_validation:",
        "unittest:pass",
        "python_test_functions:",
        "kernel_derived_acceptance:",
        "interface_capsule:",
        "cli:",
        "semantic_lint:",
    )

    def review(
        self,
        *,
        scope: str,
        contract: ContractSpec,
        item: WorkItem | None,
        feature_slice: FeatureSlice | None,
        worker_result: Any | None,
        test_result: GateResult,
    ) -> QualityReviewResult:
        evidence: List[str] = [f"quality_review:scope:{scope}"]
        diagnostics: List[Dict[str, Any]] = []

        if test_result.diagnostics:
            verdict = self._failure_verdict(test_result.diagnostics)
            evidence.append(f"quality_review:tests_failed:{verdict}")
            diagnostics.append(
                self._diag(
                    "quality_review_tests_failed",
                    feature_slice.id if feature_slice is not None else scope,
                    ",".join(_dedupe(str(diag.get("artifact", "")) for diag in test_result.diagnostics)),
                    "deterministic tests failed; review preserves the original diagnostics and blocks promotion",
                    "quality_transaction_requires_passing_tests",
                )
            )
            return QualityReviewResult(verdict=verdict, evidence=evidence, diagnostics=diagnostics)

        evidence.append("quality_review:tests_passed")
        if item is not None and worker_result is not None:
            diagnostics.extend(self._review_worker_claims(item, worker_result))
            if item.kind == "acceptance":
                diagnostics.extend(self._review_acceptance_source(worker_result, test_result, item))
        if feature_slice is not None:
            diagnostics.extend(self._review_slice_contract(feature_slice, test_result))
        if scope == "integration":
            diagnostics.extend(self._review_integration(contract, test_result))

        if diagnostics:
            verdict = NEED_MORE_TESTS if any(diag.get("code") == "review_insufficient_executable_evidence" for diag in diagnostics) else REQUEST_CHANGES
            evidence.append(f"quality_review:rejected:{verdict}")
            return QualityReviewResult(verdict=verdict, evidence=evidence, diagnostics=diagnostics)
        evidence.append("quality_review:approved")
        return QualityReviewResult(verdict=APPROVE, evidence=evidence, diagnostics=diagnostics)

    def _review_worker_claims(self, item: WorkItem, worker_result: Any) -> List[Dict[str, Any]]:
        diagnostics: List[Dict[str, Any]] = []
        allowed = set(item.allowed_artifacts)
        reported = _dedupe(worker_result.changed_files)
        unowned = [path for path in reported if path not in allowed]
        if unowned:
            diagnostics.append(
                self._diag(
                    "review_unowned_reported_change",
                    item.slice_id,
                    ",".join(unowned),
                    f"worker reported changes outside allowed_artifacts: {unowned}",
                    "artifact_ownership",
                )
            )
        if item.kind == "repair" and not any(path in allowed for path in reported):
            diagnostics.append(
                self._diag(
                    "review_repair_has_no_owned_patch",
                    item.slice_id,
                    item.repair_transaction_id or item.slice_id,
                    "repair transaction did not report an owned production patch",
                    "repair_exact_validation_required",
                )
            )
        raw = getattr(worker_result, "raw", {}) or {}
        if str(raw.get("backend", "")) == "openai" and item.kind in {"implementation", "repair", "acceptance"}:
            tool_results = list(((raw.get("raw", {}) or {}).get("tool_results", []) or []))
            tool_names = [str(record.get("name", "")) for record in tool_results]
            if "contract_snapshot" not in tool_names:
                diagnostics.append(
                    self._diag(
                        "review_missing_contract_snapshot_preflight",
                        item.slice_id,
                        ",".join(item.allowed_artifacts),
                        "OpenAI worker submitted without first calling contract_snapshot; context preflight is required.",
                        "producer_consumer_shape",
                    )
                )
            if item.dependencies and "inspect_module_api" not in tool_names:
                diagnostics.append(
                    self._diag(
                        "review_missing_dependency_api_preflight",
                        item.slice_id,
                        ",".join(item.dependencies),
                        "OpenAI worker submitted with dependencies but did not inspect dependency/canonical module APIs.",
                        "producer_consumer_shape",
                    )
                )
        return diagnostics

    def _review_acceptance_source(
        self,
        worker_result: Any,
        test_result: GateResult,
        item: WorkItem,
    ) -> List[Dict[str, Any]]:
        combined = [*worker_result.evidence, *test_result.evidence]
        if "acceptance_semantic_source:product_kernel" in combined:
            return []
        return [
            self._diag(
                "review_acceptance_not_kernel_derived",
                item.slice_id,
                ",".join(item.allowed_artifacts),
                "acceptance tests must be compiled from Product Kernel, not authored freely",
                "tests_compile_kernel_acceptance",
            )
        ]

    def _review_slice_contract(self, feature_slice: FeatureSlice, test_result: GateResult) -> List[Dict[str, Any]]:
        if not any(artifact.endswith(".py") for artifact in feature_slice.owner_artifacts):
            return []
        if self._has_executable_evidence(test_result.evidence):
            return []
        return [
            self._diag(
                "review_insufficient_executable_evidence",
                feature_slice.id,
                ",".join(feature_slice.owner_artifacts),
                "slice quality transaction passed without compile/import/smoke/test evidence",
                (feature_slice.invariant_refs or ["slice_contract"])[0],
            )
        ]

    def _review_integration(self, contract: ContractSpec, test_result: GateResult) -> List[Dict[str, Any]]:
        if not contract.required_artifacts:
            return []
        if self._has_executable_evidence(test_result.evidence):
            return []
        return [
            self._diag(
                "review_insufficient_executable_evidence",
                "integration",
                "final",
                "final quality transaction has no executable evidence",
                "tests_compile_kernel_acceptance",
            )
        ]

    def _failure_verdict(self, diagnostics: List[Dict[str, Any]]) -> str:
        codes = {str(diag.get("code", "")) for diag in diagnostics}
        if codes.intersection(self.SEMANTIC_CODES):
            return SEMANTIC_REPLAN
        return REQUEST_CHANGES

    def _has_executable_evidence(self, evidence: List[str]) -> bool:
        return any(any(item.startswith(prefix) for prefix in self.EXECUTABLE_EVIDENCE_PREFIXES) for item in evidence)

    @staticmethod
    def _diag(code: str, slice_id: str, artifact: str, message: str, invariant: str) -> Dict[str, Any]:
        return {
            "code": code,
            "slice_id": slice_id,
            "artifact": artifact,
            "message": message,
            "kernel_invariant": invariant,
        }


class QualityTransactionRunner:
    """Runs contract-aligned tests and review as one auditable decision."""

    def __init__(self, workspace_dir: str, main_workspace_dir: str | None = None):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.main_workspace_dir = os.path.abspath(main_workspace_dir or workspace_dir)
        self.root = os.path.join(self.main_workspace_dir, ".contractcoding", "quality")
        self.reviewer = QualityReviewJudge()

    def check_item(
        self,
        run_id: str,
        contract: ContractSpec,
        item: WorkItem,
        feature_slice: FeatureSlice,
        worker_result: Any,
    ) -> QualityTransactionResult:
        if item.kind in {"capsule", "interface"}:
            test_result = CapsuleJudge(self.workspace_dir).check(contract, item)
        else:
            test_result = SliceJudge(self.workspace_dir).check(feature_slice, contract)
        if item.kind == "repair" and test_result.ok:
            repair_result = RepairJudge(self.workspace_dir, self.main_workspace_dir).check(contract, item)
            test_result = GateResult(
                ok=repair_result.ok,
                evidence=[*test_result.evidence, *repair_result.evidence],
                diagnostics=[*test_result.diagnostics, *repair_result.diagnostics],
            )
        review_result = self.reviewer.review(
            scope="repair" if item.kind == "repair" else "slice",
            contract=contract,
            item=item,
            feature_slice=feature_slice,
            worker_result=worker_result,
            test_result=test_result,
        )
        return self._finish(
            run_id=run_id,
            contract=contract,
            scope="repair" if item.kind == "repair" else "slice",
            item=item,
            feature_slice=feature_slice,
            worker_result=worker_result,
            test_result=test_result,
            review_result=review_result,
        )

    def check_integration(self, run_id: str, contract: ContractSpec) -> QualityTransactionResult:
        test_result = IntegrationJudge(self.workspace_dir).check(contract)
        review_result = self.reviewer.review(
            scope="integration",
            contract=contract,
            item=None,
            feature_slice=None,
            worker_result=None,
            test_result=test_result,
        )
        return self._finish(
            run_id=run_id,
            contract=contract,
            scope="integration",
            item=None,
            feature_slice=None,
            worker_result=None,
            test_result=test_result,
            review_result=review_result,
        )

    def write(self, run_id: str, record: QualityTransactionRecord) -> str:
        filename = self._safe_id(record.item_id or record.slice_id or record.scope)
        path = os.path.join(self.root, run_id, f"{filename}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def _finish(
        self,
        *,
        run_id: str,
        contract: ContractSpec,
        scope: str,
        item: WorkItem | None,
        feature_slice: FeatureSlice | None,
        worker_result: Any | None,
        test_result: GateResult,
        review_result: QualityReviewResult,
    ) -> QualityTransactionResult:
        item_id = item.id if item is not None else "final.integration"
        slice_id = feature_slice.id if feature_slice is not None else "integration"
        record = QualityTransactionRecord(
            id=f"quality:{run_id}:{self._safe_id(item_id)}:{len(contract.quality_transactions) + 1}",
            run_id=run_id,
            scope=scope,
            item_id=item_id,
            slice_id=slice_id,
            verdict=review_result.verdict,
            changed_files=list(worker_result.changed_files if worker_result is not None else []),
            allowed_artifacts=list(item.allowed_artifacts if item is not None else contract.required_artifacts),
            locked_artifacts=list(item.locked_artifacts if item is not None else contract.test_artifacts),
            test_evidence=list(test_result.evidence),
            review_evidence=list(review_result.evidence),
            diagnostics=[*test_result.diagnostics, *review_result.diagnostics],
            team_workspace=self.workspace_dir,
            status="APPROVED" if review_result.ok else "REJECTED",
        )
        contract.quality_transactions.append(record)
        self.write(run_id, record)
        combined = GateResult(
            ok=review_result.ok,
            evidence=[*test_result.evidence, *review_result.evidence],
            diagnostics=record.diagnostics,
        )
        return QualityTransactionResult(
            ok=review_result.ok,
            record=record,
            gate_result=combined,
            test_result=test_result,
            review_result=review_result,
        )

    @staticmethod
    def _safe_id(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
        return safe.strip("_") or "quality"
