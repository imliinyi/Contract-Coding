"""Agent team execution with isolated slice workspaces and audited promotion."""

from __future__ import annotations

from dataclasses import dataclass, field
import filecmp
import json
import os
import shutil
import threading
from typing import Any, Dict, List

from ContractCoding.contract.spec import (
    ContractSpec,
    FeatureSlice,
    LLMTelemetry,
    PromotionRecord,
    TeamSpec,
    WorkItem,
    _dedupe,
)
from ContractCoding.quality.gates import GateResult
from ContractCoding.quality.semantic import compile_kernel_acceptance
from ContractCoding.quality.transaction import QualityTransactionRunner
from ContractCoding.runtime.worker import WorkerResult


@dataclass
class TeamExecutionResult:
    ok: bool
    item: WorkItem
    team: TeamSpec | None = None
    worker_result: WorkerResult | None = None
    gate_result: GateResult | None = None
    promotion: PromotionRecord | None = None
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


class TeamRuntime:
    """Runs a bounded feature-slice team and promotes only audited owner files."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.root = os.path.join(self.workspace_dir, ".contractcoding")
        self.team_root = os.path.join(self.root, "team_workspaces")
        self.promotions_root = os.path.join(self.root, "promotions")
        self._promotion_lock = threading.Lock()
        self._telemetry_lock = threading.Lock()

    def execute(self, run_id: str, contract: ContractSpec, item: WorkItem, worker: Any) -> TeamExecutionResult:
        team = self._team_for_item(contract, item)
        if team is not None:
            team.status = "RUNNING"
            team.current_item_id = item.id
        team_workspace = self.prepare_workspace(run_id, item)
        if team is not None:
            team.workspace = team_workspace

        if item.kind in {"capsule", "interface"}:
            worker_result = self._lock_interface_capsule(contract, item)
        elif item.kind == "acceptance":
            changed_files, evidence, diagnostics = compile_kernel_acceptance(team_workspace, contract, item)
            worker_result = WorkerResult(ok=not diagnostics, changed_files=changed_files, evidence=evidence, diagnostics=diagnostics)
        else:
            worker_result = worker.execute(team_workspace, contract, item)
            with self._telemetry_lock:
                self._merge_telemetry(contract, worker_result.raw)
        if not worker_result.ok:
            if team is not None:
                team.status = "BLOCKED"
            return TeamExecutionResult(
                ok=False,
                item=item,
                team=team,
                worker_result=worker_result,
                diagnostics=worker_result.diagnostics,
                evidence=worker_result.evidence,
            )

        feature_slice = self._slice_for_item(contract, item)
        quality = QualityTransactionRunner(team_workspace, self.workspace_dir).check_item(
            run_id,
            contract,
            item,
            feature_slice,
            worker_result,
        )
        if item.kind == "repair":
            self.write_repair_transaction(run_id, contract, item.repair_transaction_id)
        gate = quality.gate_result
        if not quality.ok:
            if team is not None:
                team.status = "BLOCKED"
            return TeamExecutionResult(
                ok=False,
                item=item,
                team=team,
                worker_result=worker_result,
                gate_result=gate,
                diagnostics=gate.diagnostics,
                evidence=[*worker_result.evidence, *gate.evidence],
            )

        if item.kind in {"capsule", "interface"}:
            self._lock_capsule_record(run_id, contract, item, [*worker_result.evidence, *gate.evidence])
            if team is not None:
                team.status = "CAPSULE_LOCKED"
            return TeamExecutionResult(
                ok=True,
                item=item,
                team=team,
                worker_result=worker_result,
                gate_result=gate,
                evidence=[*worker_result.evidence, *gate.evidence, f"interface_capsule_locked:{item.feature_team_id}"],
            )

        promotion = self.promote(run_id, contract, item, team_workspace, worker_result.changed_files, [*worker_result.evidence, *gate.evidence])
        if promotion.status != "PROMOTED":
            if team is not None:
                team.status = "BLOCKED"
            return TeamExecutionResult(
                ok=False,
                item=item,
                team=team,
                worker_result=worker_result,
                gate_result=gate,
                promotion=promotion,
                diagnostics=[{"code": "promotion_blocked", "slice_id": item.slice_id, "message": promotion.summary}],
                evidence=[*worker_result.evidence, *gate.evidence],
            )

        if team is not None:
            team.status = "VERIFIED"
        return TeamExecutionResult(
            ok=True,
            item=item,
            team=team,
            worker_result=worker_result,
            gate_result=gate,
            promotion=promotion,
            evidence=[*worker_result.evidence, *gate.evidence, promotion.summary],
        )

    def _lock_interface_capsule(self, contract: ContractSpec, item: WorkItem) -> WorkerResult:
        capsule = next(
            (candidate for candidate in contract.interface_capsules if candidate.team_id == item.feature_team_id),
            None,
        )
        if capsule is None:
            return WorkerResult(
                ok=False,
                diagnostics=[
                    {
                        "code": "interface_capsule_missing",
                        "artifact": item.feature_team_id,
                        "message": f"No interface capsule exists for {item.feature_team_id}",
                    }
                ],
            )
        return WorkerResult(
            ok=True,
            changed_files=[],
            evidence=[
                f"interface_capsule_intent:{capsule.id}",
                f"interface_capsule_version:{capsule.version}",
                f"interface_capsule_capabilities:{len(capsule.capabilities)}",
                f"interface_capsule_examples:{len(capsule.examples)}",
            ],
        )

    def _lock_capsule_record(
        self,
        run_id: str,
        contract: ContractSpec,
        item: WorkItem,
        evidence: List[str],
    ) -> None:
        capsule = next(
            (candidate for candidate in contract.interface_capsules if candidate.team_id == item.feature_team_id),
            None,
        )
        if capsule is None:
            return
        capsule.status = "LOCKED"
        capsule.lock_item_id = item.id
        capsule.lock_evidence = _dedupe([*capsule.lock_evidence, *evidence])
        self.write_interface_capsule(capsule)
        for state in contract.team_states:
            if state.team_id == item.feature_team_id:
                state.phase = "build"
                state.frozen_interfaces = _dedupe([*state.frozen_interfaces, capsule.id])
                state.waiting_on_interfaces = [
                    ref
                    for ref in state.waiting_on_interfaces
                    if ref != capsule.id and ref != f"capsule:{item.feature_team_id}"
                ]
            for message in state.mailbox:
                if message.get("interface_ref") == capsule.id:
                    message["status"] = "LOCKED"

    def write_interface_capsule(self, capsule: Any) -> str:
        path = os.path.join(self.root, "interface_capsules", f"{capsule.id.replace(':', '_')}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(capsule.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def prepare_workspace(self, run_id: str, item: WorkItem) -> str:
        team_workspace = os.path.join(self.team_root, run_id, self._safe_id(item.slice_id))
        if os.path.exists(team_workspace):
            shutil.rmtree(team_workspace)
        os.makedirs(team_workspace, exist_ok=True)
        for root, dirs, files in os.walk(self.workspace_dir):
            rel_root = os.path.relpath(root, self.workspace_dir)
            if self._excluded(rel_root):
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if not self._excluded(os.path.join(rel_root, name))]
            for name in files:
                rel = os.path.normpath(os.path.join(rel_root, name)).replace("\\", "/")
                if rel == "." or self._excluded(rel):
                    continue
                src = os.path.join(root, name)
                dst = os.path.join(team_workspace, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        return team_workspace

    def promote(
        self,
        run_id: str,
        contract: ContractSpec,
        item: WorkItem,
        team_workspace: str,
        reported_changed: List[str],
        evidence: List[str],
    ) -> PromotionRecord:
        with self._promotion_lock:
            owned = _dedupe(item.allowed_artifacts)
            changed = self._changed_owner_files(team_workspace, owned)
            changed_files = _dedupe([*reported_changed, *changed])
            unowned = [path for path in changed_files if path not in owned]
            missing = [path for path in owned if not os.path.exists(os.path.join(team_workspace, path))]
            conflicts = [f"unowned change: {path}" for path in unowned]
            if item.kind == "repair" and not changed:
                conflicts.append("repair transaction produced no owned-file patch")

            promotion = PromotionRecord(
                id=f"promotion:{run_id}:{self._safe_id(item.slice_id)}:{len(contract.promotions) + 1}",
                run_id=run_id,
                slice_id=item.slice_id,
                changed_files=changed_files,
                owned_files=[path for path in changed_files if path in owned],
                unowned_files=unowned,
                missing_files=missing,
                conflicts=conflicts,
                evidence=list(evidence),
                team_workspace=team_workspace,
                status="PROMOTED" if not missing and not conflicts else "BLOCKED",
                summary="",
            )
            if promotion.status == "PROMOTED":
                for path in owned:
                    src = os.path.join(team_workspace, path)
                    if not os.path.exists(src):
                        continue
                    dst = os.path.join(self.workspace_dir, path)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                promotion.summary = f"promoted {len(promotion.owned_files)} owned files for {item.slice_id}"
            else:
                promotion.summary = "; ".join([*missing, *conflicts]) or "promotion blocked"
            contract.promotions.append(promotion)
            self.write_promotion(run_id, promotion)
            return promotion

    def write_promotion(self, run_id: str, promotion: PromotionRecord) -> str:
        path = os.path.join(self.promotions_root, run_id, f"{self._safe_id(promotion.slice_id)}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(promotion.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def write_repair_transaction(self, run_id: str, contract: ContractSpec, transaction_id: str) -> str:
        transaction = next((candidate for candidate in contract.repair_transactions if candidate.id == transaction_id), None)
        if transaction is None:
            return ""
        path = os.path.join(self.root, "repairs", run_id, f"{transaction.id.replace(':', '_')}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(transaction.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def _changed_owner_files(self, team_workspace: str, owned: List[str]) -> List[str]:
        changed: List[str] = []
        for path in owned:
            team_path = os.path.join(team_workspace, path)
            main_path = os.path.join(self.workspace_dir, path)
            if not os.path.exists(team_path):
                continue
            if not os.path.exists(main_path) or not filecmp.cmp(team_path, main_path, shallow=False):
                changed.append(path)
        return changed

    def _team_for_item(self, contract: ContractSpec, item: WorkItem) -> TeamSpec | None:
        if item.team_id:
            for team in contract.teams:
                if team.id == item.team_id:
                    return team
        for team in contract.teams:
            if team.slice_id == item.slice_id:
                return team
        return None

    def _slice_for_item(self, contract: ContractSpec, item: WorkItem) -> FeatureSlice:
        feature_slice = contract.slice_by_id().get(item.slice_id)
        if feature_slice is not None:
            return feature_slice
        return FeatureSlice(
            id=item.slice_id,
            title=item.title,
            owner_artifacts=list(item.allowed_artifacts),
            fixture_refs=["smoke_workspace"],
            invariant_refs=[item.repair_transaction_id or "repair_transaction"],
            acceptance_refs=["compile_import"],
            done_contract=["Repair transaction patch compiles and changes at least one owned artifact."],
            phase=item.phase,
            conflict_keys=list(item.conflict_keys),
        )

    @staticmethod
    def _merge_telemetry(contract: ContractSpec, raw: Dict[str, Any]) -> None:
        if not raw:
            return
        telemetry: LLMTelemetry = contract.llm_telemetry
        backend = str(raw.get("backend", "") or raw.get("raw", {}).get("backend", ""))
        if backend:
            telemetry.backend = backend
        telemetry.prompt_tokens += int(raw.get("prompt_tokens", 0) or 0)
        telemetry.completion_tokens += int(raw.get("completion_tokens", 0) or 0)
        raw_payload = raw.get("raw", {}) if isinstance(raw.get("raw", {}), dict) else {}
        telemetry.tool_calls += int(raw_payload.get("tool_calls", 0) or raw_payload.get("tool_call_count", 0) or 0)
        telemetry.tool_iterations += int(raw_payload.get("tool_iterations", 0) or raw_payload.get("tool_iteration_count", 0) or 0)
        if raw_payload.get("timeout") or raw_payload.get("timed_out"):
            telemetry.timeouts += 1
        if raw_payload.get("infra_failure") or raw_payload.get("error"):
            telemetry.errors += 1

    @staticmethod
    def _excluded(rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/").strip("/")
        if normalized in {"", "."}:
            return False
        return (
            normalized == ".contractcoding"
            or normalized.startswith(".contractcoding/")
            or normalized == ".git"
            or normalized.startswith(".git/")
            or "__pycache__" in normalized.split("/")
        )

    @staticmethod
    def _safe_id(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
