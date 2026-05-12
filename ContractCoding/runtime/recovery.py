"""Central repair transaction and replan policy."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List

from ContractCoding.contract.spec import AgentSpec, ContractSpec, RepairTransaction, ReplanRecord, TeamSpec, WorkItem, _dedupe
from ContractCoding.quality.semantic import semantic_kernel_delta
from ContractCoding.runtime.store import RunRecord


class RecoveryCoordinator:
    """Owns final integration recovery.

    Ordinary feature teams only fix their local slice gate. Final integration
    failures enter this central transaction lane, where locked tests, allowed
    artifacts, no-progress detection, and replans are explicit.
    """

    def __init__(self, workspace_dir: str, max_attempts: int = 2, max_replans: int = 1):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.max_attempts = max(1, int(max_attempts or 2))
        self.max_replans = max(0, int(max_replans or 1))

    def handle_final_failure(self, run: RunRecord, diagnostics: List[Dict[str, Any]]) -> bool:
        fingerprint = self.fingerprint(diagnostics)
        if self._requires_semantic_replan(diagnostics):
            return self._open_semantic_replan_or_block(
                run,
                fingerprint,
                diagnostics,
                reason="failure classified as semantic kernel or acceptance-source conflict",
            )
        transaction = self._transaction_for(run.contract, fingerprint)
        if transaction is None:
            transaction = self._new_transaction(run.contract, fingerprint, diagnostics)
            run.contract.repair_transactions.append(transaction)
        elif not transaction.allowed_artifacts:
            expanded = self._allowed_repair_artifacts(run.contract, diagnostics)
            if expanded:
                transaction.allowed_artifacts = expanded
                transaction.pre_patch_artifact_hashes = self._artifact_hashes(expanded)
                transaction.validation_commands = self._validation_commands(run.contract, diagnostics)
                transaction.attempts = 0
                transaction.no_progress_count = 0
                transaction.status = "OPEN"
                transaction.evidence.append(f"recovered empty repair scope: {', '.join(expanded)}")
        if transaction.status == "HUMAN_REQUIRED":
            return False
        if transaction.attempts >= self.max_attempts:
            return self._open_replan_or_block(run, transaction, diagnostics, reason="same fingerprint repeated after repair attempts")
        transaction.status = "OPEN"
        transaction.attempts += 1
        self._ensure_repair_team(run.contract)
        self._append_repair_item(run.contract, transaction, diagnostics)
        self.write_transaction(run.id, transaction)
        return True

    def handle_item_blocker(self, run: RunRecord, item: WorkItem, diagnostics: List[Dict[str, Any]]) -> bool:
        """Turn explicit dependency/interface blockers into central repair work.

        Worker-local gate failures should stay local. A report_blocker with
        required producer artifacts is different: the current slice is telling
        the runtime that its executable contract cannot be satisfied by editing
        only its own owner files. That belongs in the central repair lane, after
        which the blocked slice is retried against the repaired producers.
        """

        if item.kind == "repair" or not self._is_dependency_blocker(diagnostics):
            return False
        fingerprint = self.fingerprint(
            [
                {
                    **diagnostic,
                    "blocked_item_id": item.id,
                    "blocked_slice_id": item.slice_id,
                }
                for diagnostic in diagnostics
            ]
        )
        transaction = self._transaction_for(run.contract, fingerprint)
        if transaction is None:
            transaction = self._new_transaction(run.contract, fingerprint, diagnostics)
            transaction.evidence.append(f"opened from blocker in {item.id}")
            run.contract.repair_transactions.append(transaction)
        if transaction.status == "HUMAN_REQUIRED":
            return False
        if transaction.attempts >= self.max_attempts:
            return self._open_replan_or_block(run, transaction, diagnostics, reason="same dependency blocker repeated")
        transaction.status = "OPEN"
        transaction.attempts += 1
        self._ensure_repair_team(run.contract)
        repair_item = self._append_repair_item(run.contract, transaction, diagnostics)
        if repair_item.id not in item.dependencies:
            item.dependencies.append(repair_item.id)
        item.status = "PENDING"
        item.diagnostics = [
            {
                "code": "waiting_on_repair_transaction",
                "repair_transaction_id": transaction.id,
                "repair_item_id": repair_item.id,
                "blocked_item_id": item.id,
                "message": "dependency/interface blocker routed to central repair transaction",
            }
        ]
        self.write_transaction(run.id, transaction)
        return True

    def handle_repair_failure(self, run: RunRecord, item: WorkItem, diagnostics: List[Dict[str, Any]]) -> bool:
        transaction = self._transaction_by_id(run.contract, item.repair_transaction_id)
        if transaction is None:
            item.status = "BLOCKED"
            return False
        if self._is_scope_blocker(diagnostics):
            expanded = self._expanded_allowed_artifacts(run.contract, transaction, diagnostics)
            if expanded != transaction.allowed_artifacts:
                transaction.allowed_artifacts = expanded
                transaction.pre_patch_artifact_hashes = self._artifact_hashes(expanded)
                transaction.validation_commands = self._validation_commands(run.contract, diagnostics)
                transaction.evidence.append(f"expanded repair scope: {', '.join(expanded)}")
                item.status = "SUPERSEDED"
                transaction.attempts += 1
                next_item = self._append_repair_item(run.contract, transaction, diagnostics)
                self._redirect_waiting_dependents(run.contract, item.id, next_item.id)
                self.write_transaction(run.id, transaction)
                return True
        transaction.no_progress_count += 1
        transaction.evidence.append(f"repair item {item.id} failed: {self._brief(diagnostics)}")
        item.status = "SUPERSEDED"
        if transaction.no_progress_count >= self.max_attempts:
            if self._requires_semantic_replan(diagnostics):
                return self._open_semantic_replan_or_block(
                    run,
                    transaction.failure_fingerprint,
                    diagnostics,
                    reason="repair transaction repeated a semantic failure fingerprint",
                    transaction=transaction,
                )
            return self._open_replan_or_block(run, transaction, diagnostics, reason="repair transaction made no patch progress")
        transaction.attempts += 1
        next_item = self._append_repair_item(run.contract, transaction, diagnostics)
        self._redirect_waiting_dependents(run.contract, item.id, next_item.id)
        self.write_transaction(run.id, transaction)
        return True

    def _open_semantic_replan_or_block(
        self,
        run: RunRecord,
        fingerprint: str,
        diagnostics: List[Dict[str, Any]],
        reason: str,
        transaction: RepairTransaction | None = None,
    ) -> bool:
        existing_replans = [replan for replan in run.contract.replans if replan.failure_fingerprint == fingerprint]
        if len(existing_replans) >= self.max_replans:
            if transaction is not None:
                transaction.status = "HUMAN_REQUIRED"
                transaction.evidence.append(f"human required: {reason}")
                self.write_transaction(run.id, transaction)
            return False
        delta = semantic_kernel_delta(run.contract, diagnostics)
        affected = self._affected_slices_from_kernel_delta(run.contract, delta)
        if not affected:
            affected = self._affected_slices(run.contract, diagnostics)
        if not affected:
            if transaction is not None:
                transaction.status = "HUMAN_REQUIRED"
                transaction.evidence.append("human required: semantic replan had no affected slices")
                self.write_transaction(run.id, transaction)
            return False
        self._apply_kernel_delta(run.contract, delta)
        replan = ReplanRecord(
            id=f"replan:{fingerprint[:12]}:{len(run.contract.replans) + 1}",
            reason=reason,
            affected_slices=affected,
            failure_fingerprint=fingerprint,
            kernel_delta=delta,
            status="APPLIED",
        )
        run.contract.replans.append(replan)
        if transaction is not None:
            transaction.status = "REPLANNED"
            transaction.evidence.append(f"semantic replan opened for slices: {', '.join(affected)}")
            self.write_transaction(run.id, transaction)
        for work_item in run.contract.work_items:
            if work_item.slice_id in affected and work_item.kind != "repair":
                work_item.status = "PENDING"
                work_item.diagnostics.append({"code": "semantic_replanned", "message": reason, "kernel_delta": delta})
        return True

    def write_transaction(self, run_id: str, transaction: RepairTransaction) -> str:
        path = os.path.join(self.workspace_dir, ".contractcoding", "repairs", run_id, f"{transaction.id.replace(':', '_')}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(transaction.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def _open_replan_or_block(
        self,
        run: RunRecord,
        transaction: RepairTransaction,
        diagnostics: List[Dict[str, Any]],
        reason: str,
    ) -> bool:
        existing_replans = [replan for replan in run.contract.replans if replan.failure_fingerprint == transaction.failure_fingerprint]
        if len(existing_replans) >= self.max_replans:
            transaction.status = "HUMAN_REQUIRED"
            transaction.evidence.append(f"human required: {reason}")
            self.write_transaction(run.id, transaction)
            return False
        affected = self._affected_slices(run.contract, diagnostics)
        if not affected:
            transaction.status = "HUMAN_REQUIRED"
            transaction.evidence.append("human required: no legal affected slice could be derived")
            self.write_transaction(run.id, transaction)
            return False
        replan = ReplanRecord(
            id=f"replan:{transaction.failure_fingerprint[:12]}:{len(run.contract.replans) + 1}",
            reason=reason,
            affected_slices=affected,
            failure_fingerprint=transaction.failure_fingerprint,
            kernel_delta=semantic_kernel_delta(run.contract, diagnostics),
            status="APPLIED",
        )
        run.contract.replans.append(replan)
        transaction.status = "REPLANNED"
        transaction.evidence.append(f"replan opened for slices: {', '.join(affected)}")
        for work_item in run.contract.work_items:
            if work_item.slice_id in affected and work_item.kind != "repair":
                work_item.status = "PENDING"
                work_item.diagnostics.append({"code": "replanned", "message": reason})
        self.write_transaction(run.id, transaction)
        return True

    def _new_transaction(self, contract: ContractSpec, fingerprint: str, diagnostics: List[Dict[str, Any]]) -> RepairTransaction:
        allowed = self._allowed_repair_artifacts(contract, diagnostics)
        if not allowed:
            allowed = self._fallback_repair_artifacts(contract)
        locked = list(contract.test_artifacts)
        validation_commands = self._validation_commands(contract, diagnostics)
        root_invariant = str((diagnostics or [{}])[0].get("kernel_invariant") or (diagnostics or [{}])[0].get("code") or "final_acceptance")
        return RepairTransaction(
            id=f"repair:{fingerprint[:12]}",
            failure_fingerprint=fingerprint,
            root_invariant=root_invariant,
            allowed_artifacts=allowed,
            locked_tests=locked,
            validation_commands=validation_commands,
            pre_patch_artifact_hashes=self._artifact_hashes(allowed),
            patch_plan=[
                "cluster final diagnostics by artifact and invariant",
                "patch only allowed_artifacts",
                "leave locked_tests unchanged",
                "validate locked exact commands in the isolated repair workspace",
                "promote only after exact validation passes",
            ],
            expected_behavior_delta="Make frozen Product Kernel acceptance pass without weakening locked tests.",
        )

    @staticmethod
    def _requires_semantic_replan(diagnostics: List[Dict[str, Any]]) -> bool:
        semantic_codes = {
            "forbidden_value_object_equivalence",
            "ungrounded_acceptance_assertion",
            "ungrounded_numeric_acceptance_assertion",
            "semantic_kernel_conflict",
            "kernel_ontology_conflict",
        }
        text = "\n".join(
            " ".join(
                [
                    str(diagnostic.get("code", "")),
                    str(diagnostic.get("kernel_invariant", "")),
                    str(diagnostic.get("message", "")),
                    str(diagnostic.get("reason", "")),
                ]
            ).lower()
            for diagnostic in diagnostics
        )
        if any(str(diagnostic.get("code", "")) in semantic_codes for diagnostic in diagnostics):
            return True
        if "latitude out of range" in text and ("coordinate" in text or "geopoint" in text or "tests_compile_kernel_acceptance" in text):
            return True
        if "acceptance_has_kernel_source" in text:
            return True
        return False

    @staticmethod
    def _affected_slices_from_kernel_delta(contract: ContractSpec, delta: Dict[str, Any]) -> List[str]:
        raw: List[str] = []
        for key in ("ontology_patch", "acceptance_patch"):
            section = delta.get(key, {})
            if isinstance(section, dict):
                raw.extend(str(value) for value in section.get("affected_semantic_contracts", []) or [])
        available = {feature_slice.id for feature_slice in contract.feature_slices}
        return _dedupe(value for value in raw if value in available)

    @staticmethod
    def _apply_kernel_delta(contract: ContractSpec, delta: Dict[str, Any]) -> None:
        patches = list((contract.product_kernel.ontology or {}).get("replan_patches", []) or [])
        patches.append(delta)
        contract.product_kernel.ontology["replan_patches"] = patches
        contract.product_kernel.status = "FROZEN"

    def _append_repair_item(self, contract: ContractSpec, transaction: RepairTransaction, diagnostics: List[Dict[str, Any]]) -> WorkItem:
        item = WorkItem(
            id=f"{transaction.id}:attempt:{transaction.attempts}",
            slice_id=transaction.id,
            title="Central final repair transaction",
            allowed_artifacts=list(transaction.allowed_artifacts),
            dependencies=[],
            kind="repair",
            phase="repair.transaction",
            team_id="team:repair",
            conflict_keys=[f"artifact:{artifact}" for artifact in transaction.allowed_artifacts],
            locked_artifacts=list(transaction.locked_tests),
            repair_transaction_id=transaction.id,
            diagnostics=list(diagnostics),
        )
        if item.id not in contract.item_by_id():
            contract.work_items.append(item)
        return item

    @staticmethod
    def _ensure_repair_team(contract: ContractSpec) -> None:
        if any(team.id == "team:repair" for team in contract.teams):
            return
        contract.teams.append(
            TeamSpec(
                id="team:repair",
                slice_id="repair",
                phase="repair.transaction",
                agents=[
                    AgentSpec(
                        id="repair:diagnostician",
                        role="failure_cluster_reviewer",
                        skills=[
                            "repair_transaction",
                            "dependency_interface_consumption",
                            "replan_failure_cluster",
                            "judge_contract_verification",
                            "evidence_submission_protocol",
                        ],
                    ),
                    AgentSpec(
                        id="repair:patcher",
                        role="bounded_repair_worker",
                        skills=[
                            "repair_transaction",
                            "dependency_interface_consumption",
                            "code_generation_slice",
                            "code_test_slice",
                            "tool_use_protocol",
                            "evidence_submission_protocol",
                        ],
                    ),
                ],
            )
        )

    @staticmethod
    def _transaction_for(contract: ContractSpec, fingerprint: str) -> RepairTransaction | None:
        return next((transaction for transaction in contract.repair_transactions if transaction.failure_fingerprint == fingerprint), None)

    @staticmethod
    def _transaction_by_id(contract: ContractSpec, transaction_id: str) -> RepairTransaction | None:
        return next((transaction for transaction in contract.repair_transactions if transaction.id == transaction_id), None)

    @staticmethod
    def _affected_slices(contract: ContractSpec, diagnostics: List[Dict[str, Any]]) -> List[str]:
        by_artifact: Dict[str, str] = {}
        for feature_slice in contract.feature_slices:
            for artifact in feature_slice.owner_artifacts:
                by_artifact[artifact] = feature_slice.id
        affected: List[str] = []
        for diagnostic in diagnostics:
            artifact = str(diagnostic.get("artifact", ""))
            if artifact in by_artifact:
                affected.append(by_artifact[artifact])
            for required in RecoveryCoordinator._required_artifacts_from_diagnostic(diagnostic):
                normalized = RecoveryCoordinator._contract_relative_path(required, contract.required_artifacts)
                if normalized in by_artifact:
                    affected.append(by_artifact[normalized])
        if not affected:
            affected = [
                feature_slice.id
                for feature_slice in contract.feature_slices
                if feature_slice.id != "kernel_acceptance"
                and feature_slice.owner_artifacts
                and not all(artifact in contract.test_artifacts for artifact in feature_slice.owner_artifacts)
            ]
        return _dedupe(affected)

    def _allowed_repair_artifacts(self, contract: ContractSpec, diagnostics: List[Dict[str, Any]]) -> List[str]:
        by_artifact = {
            artifact: feature_slice.id
            for feature_slice in contract.feature_slices
            for artifact in feature_slice.owner_artifacts
        }
        candidates: List[str] = []
        message_text = "\n".join(
            "\n".join([str(diagnostic.get("message", "")), str(diagnostic.get("reason", ""))])
            for diagnostic in diagnostics
        )
        for diagnostic in diagnostics:
            artifact = str(diagnostic.get("artifact", ""))
            if artifact in by_artifact and artifact not in contract.test_artifacts:
                candidates.append(artifact)
            for required in self._required_artifacts_from_diagnostic(diagnostic):
                normalized = self._contract_relative_path(required, contract.required_artifacts)
                if normalized in by_artifact and normalized not in contract.test_artifacts:
                    candidates.append(normalized)
        for path in self._paths_in_text(message_text):
            normalized = self._contract_relative_path(path, contract.required_artifacts)
            if normalized in by_artifact and normalized not in contract.test_artifacts:
                candidates.append(normalized)
        if candidates:
            return _dedupe([artifact for artifact in candidates if artifact.endswith(".py")])
        affected = set(self._affected_slices(contract, diagnostics))
        if affected:
            scoped = _dedupe(
                artifact
                for feature_slice in contract.feature_slices
                if feature_slice.id in affected and feature_slice.id != "kernel_acceptance"
                for artifact in feature_slice.owner_artifacts
                if artifact.endswith(".py") and artifact not in contract.test_artifacts
            )
            if scoped:
                return scoped
        return self._fallback_repair_artifacts(contract)

    @staticmethod
    def _fallback_repair_artifacts(contract: ContractSpec) -> List[str]:
        return _dedupe(
            artifact
            for artifact in contract.required_artifacts
            if artifact.endswith(".py") and artifact not in contract.test_artifacts
        )[:12]

    def _expanded_allowed_artifacts(
        self,
        contract: ContractSpec,
        transaction: RepairTransaction,
        diagnostics: List[Dict[str, Any]],
    ) -> List[str]:
        return _dedupe([*transaction.allowed_artifacts, *self._allowed_repair_artifacts(contract, diagnostics)])

    def _validation_commands(self, contract: ContractSpec, diagnostics: List[Dict[str, Any]]) -> List[List[str]]:
        message_text = "\n".join(
            "\n".join([str(diagnostic.get("message", "")), str(diagnostic.get("reason", ""))])
            for diagnostic in diagnostics
        )
        exact_tests = [
            matched
            for matched in (
                self._contract_relative_path(path, contract.test_artifacts) for path in self._paths_in_text(message_text)
            )
            if matched and os.path.exists(os.path.join(self.workspace_dir, matched))
        ]
        if not exact_tests:
            exact_tests = [
                str(diagnostic.get("artifact", ""))
                for diagnostic in diagnostics
                if str(diagnostic.get("artifact", "")) in contract.test_artifacts
                and os.path.exists(os.path.join(self.workspace_dir, str(diagnostic.get("artifact", ""))))
            ]
        commands: List[List[str]] = []
        for test in _dedupe(exact_tests or contract.test_artifacts):
            if not os.path.exists(os.path.join(self.workspace_dir, test)):
                continue
            module = self._test_module(test)
            if module:
                commands.append(["{python}", "-m", "unittest", module, "-v"])
        existing_tests = [test for test in contract.test_artifacts if os.path.exists(os.path.join(self.workspace_dir, test))]
        if existing_tests:
            commands.append(["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"])
        return commands or [["{python}", "-m", "compileall", "."]]

    def _artifact_hashes(self, artifacts: List[str]) -> Dict[str, str]:
        hashes: Dict[str, str] = {}
        for artifact in artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as handle:
                    hashes[artifact] = hashlib.sha256(handle.read()).hexdigest()
            except OSError:
                continue
        return hashes

    @staticmethod
    def _paths_in_text(text: str) -> List[str]:
        paths: List[str] = []
        for match in re.findall(r"([/A-Za-z0-9_./-]+\.py)", text):
            normalized = os.path.normpath(match.strip().strip("'\"`:,;()[]{}")).replace("\\", "/")
            if normalized.startswith("./"):
                normalized = normalized[2:]
            if normalized not in paths:
                paths.append(normalized)
        return paths

    @staticmethod
    def _contract_relative_path(path: str, artifacts: List[str]) -> str:
        normalized = os.path.normpath(str(path or "")).replace("\\", "/").lstrip("/")
        for artifact in artifacts:
            candidate = artifact.replace("\\", "/")
            if normalized == candidate or normalized.endswith("/" + candidate):
                return candidate
        return normalized

    @staticmethod
    def _test_module(path: str) -> str:
        normalized = path.replace("\\", "/")
        if not normalized.endswith(".py"):
            return ""
        stem = normalized[:-3]
        parts = [part for part in stem.split("/") if part]
        if all(part.isidentifier() for part in parts):
            return ".".join(parts)
        return ""

    @staticmethod
    def fingerprint(diagnostics: List[Dict[str, Any]]) -> str:
        material = "|".join(
            (
                f"{diag.get('code')}:{diag.get('tool_name')}:{diag.get('artifact')}:"
                f"{diag.get('kernel_invariant')}:{diag.get('message')}:{diag.get('reason')}:"
                f"{diag.get('blocked_item_id')}:{diag.get('blocked_slice_id')}:"
                f"{','.join(RecoveryCoordinator._required_artifacts_from_diagnostic(diag))}"
            )
            for diag in diagnostics
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_dependency_blocker(diagnostics: List[Dict[str, Any]]) -> bool:
        for diagnostic in diagnostics:
            if str(diagnostic.get("tool_name", "")) != "report_blocker":
                continue
            blocker_type = str(diagnostic.get("blocker_type") or diagnostic.get("arguments", {}).get("blocker_type") or "")
            if blocker_type in {"missing_interface", "producer_contract_conflict", "dependency_contract_conflict"}:
                return True
            if RecoveryCoordinator._required_artifacts_from_diagnostic(diagnostic):
                return True
        return False

    @staticmethod
    def _is_scope_blocker(diagnostics: List[Dict[str, Any]]) -> bool:
        for diagnostic in diagnostics:
            if str(diagnostic.get("tool_name", "")) != "report_blocker":
                continue
            blocker_type = str(diagnostic.get("blocker_type") or diagnostic.get("arguments", {}).get("blocker_type") or "")
            if blocker_type in {"out_of_scope_repair", "missing_interface", "producer_contract_conflict"}:
                return bool(RecoveryCoordinator._required_artifacts_from_diagnostic(diagnostic))
        return False

    @staticmethod
    def _required_artifacts_from_diagnostic(diagnostic: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for source in (diagnostic, diagnostic.get("arguments", {}) if isinstance(diagnostic.get("arguments"), dict) else {}):
            for key in ("required_artifacts", "producer_artifacts", "affected_artifacts"):
                raw = source.get(key)
                if isinstance(raw, list):
                    values.extend(str(value) for value in raw)
                elif isinstance(raw, str):
                    values.append(raw)
        return _dedupe(values)

    @staticmethod
    def _redirect_waiting_dependents(contract: ContractSpec, old_item_id: str, new_item_id: str) -> None:
        for work_item in contract.work_items:
            if old_item_id not in work_item.dependencies:
                continue
            work_item.dependencies = [new_item_id if dep == old_item_id else dep for dep in work_item.dependencies]

    @staticmethod
    def _brief(diagnostics: List[Dict[str, Any]]) -> str:
        if not diagnostics:
            return "no diagnostics"
        first = diagnostics[0]
        message = first.get("message") or first.get("reason") or first.get("arguments", {}).get("reason", "")
        return f"{first.get('code') or first.get('tool_name')} {first.get('artifact')} {str(message)[:160]}"
