"""Deterministic contract auditing.

The auditor does not ask an LLM whether a claim is true. It checks structured
claims against registry state and derives obligations from the executable
contract kernel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from ..contract.capsule import CapsuleStatus
from ..contract.kernel import ContractKernel
from ..contract.operation import (
    ContractObligation,
    ContractOperation,
    ObligationKind,
    ObligationStatus,
    OperationKind,
)
from ..registry import RegistryTool
from ..registry.backend import RegistryPath


def split_capsule_ref(ref: str) -> Tuple[str, str]:
    raw = ref.replace("capsule:", "", 1)
    if "/" in raw:
        return tuple(raw.split("/", 1))  # type: ignore[return-value]
    if ":" in raw:
        return tuple(raw.split(":", 1))  # type: ignore[return-value]
    return ("", raw)


@dataclass
class ContractAuditor:
    tool: RegistryTool

    def audit_operation(self, op: ContractOperation) -> List[str]:
        errors: List[str] = []
        if not op.from_team:
            errors.append("from_team required")
        if not op.target_ref:
            errors.append("target_ref required")
        if not isinstance(op.payload, dict):
            errors.append("payload must be an object")

        if op.kind in (
            OperationKind.DECLARE_API,
            OperationKind.PUBLISH_CAPSULE,
            OperationKind.SUBMIT_EVIDENCE,
        ) and not op.evidence_refs:
            errors.append(f"{op.kind.value} requires evidence_refs")

        for ref in op.evidence_refs:
            errors.extend(self._check_evidence_ref(ref, op.from_team))

        if op.kind == OperationKind.DECLARE_API:
            errors.extend(self._audit_declare_api(op))
        elif op.kind == OperationKind.DECLARE_DEPENDENCY:
            errors.extend(self._audit_declare_dependency(op))
        elif op.kind == OperationKind.SUBMIT_EVIDENCE:
            errors.extend(self._audit_submit_evidence(op))
        return errors

    def derive_obligations(self, kernel: ContractKernel) -> List[ContractObligation]:
        obligations: Dict[tuple[str, str, str, tuple[str, ...]], ContractObligation] = {}
        for item in kernel.all_work_items():
            status = item.status.value if hasattr(item.status, "value") else str(item.status)
            if status not in ("pending", "active"):
                continue
            for cap_ref in item.capsule_dependencies:
                owner, capability = split_capsule_ref(cap_ref)
                key = f"{owner}/{capability}" if owner else capability
                cap = kernel.capsules.get(key)
                if cap is None or cap.status not in (
                    CapsuleStatus.DRAFT,
                    CapsuleStatus.LOCKED,
                    CapsuleStatus.EVOLVED,
                ):
                    obligation = ContractObligation.new(
                        kind=ObligationKind.MISSING_CAPSULE,
                        team_id=item.team_id,
                        target_team=owner,
                        target_ref=cap_ref,
                        task_ids=[item.task_id],
                        reason=f"missing capsule dependency: {cap_ref}",
                    )
                    obligations[obligation.key()] = obligation
            if item.writes and not item.validation_commands:
                obligation = ContractObligation.new(
                    kind=ObligationKind.VALIDATION_MISSING,
                    team_id=item.team_id,
                    target_ref=f"work:{item.work_id}",
                    task_ids=[item.task_id],
                    reason="work item declares writes but no validation_commands",
                )
                obligations[obligation.key()] = obligation
        return list(obligations.values())

    def resolve_obligations(
        self,
        obligations: Iterable[ContractObligation],
        accepted_ops: Iterable[ContractOperation],
    ) -> List[ContractObligation]:
        out = list(obligations)
        for op in accepted_ops:
            if op.kind == OperationKind.SUBMIT_EVIDENCE:
                targets = {op.target_ref, *[f"work:{tid}" for tid in op.related_task_ids]}
                for obligation in out:
                    if obligation.status != ObligationStatus.OPEN:
                        continue
                    if obligation.target_ref in targets or set(obligation.task_ids) & set(op.related_task_ids):
                        obligation.resolve(op.evidence_refs)
        return out

    # ------------------------------------------------------------------ checks

    def _check_evidence_ref(self, ref: str, team_id: str) -> List[str]:
        if not ref:
            return ["empty evidence ref"]
        if ref.startswith(("validation:", "contract:", "op:", "event:")):
            return []
        if ref.startswith("evidence:"):
            evidence = self.tool.get_validation_evidence(ref)
            if evidence is None:
                return [f"validation evidence not found: {ref}"]
            if not evidence.passed:
                return [f"validation evidence did not pass: {ref}"]
            return []
        if ref.startswith("capsule:"):
            owner, capability = split_capsule_ref(ref)
            if not owner or self.tool.get_capsule(owner, capability) is None:
                return [f"capsule evidence not found: {ref}"]
            return []
        if ref.startswith("/workspace/"):
            path = RegistryPath(ref)
        elif ref.startswith("workspace/"):
            path = RegistryPath("/" + ref)
        else:
            path = RegistryPath(f"/workspace/{team_id}/{ref}")
        return [] if self.tool.backend.exists(path) else [f"evidence file not found: {ref}"]

    def _audit_declare_api(self, op: ContractOperation) -> List[str]:
        errors: List[str] = []
        ref_owner, _ = split_capsule_ref(op.target_ref)
        owner = op.target_team or ref_owner or op.from_team
        if owner != op.from_team:
            errors.append("declare_api may only be submitted by the owning team")
        capability = str(op.payload.get("capability") or op.target_ref.split("/")[-1])
        if not capability:
            errors.append("declare_api requires payload.capability")
        symbols = [str(v) for v in op.payload.get("symbols", []) or []]
        files = [str(v) for v in op.payload.get("files", []) or []]
        if not symbols and not op.payload.get("interface_def"):
            errors.append("declare_api requires symbols or interface_def")
        for file_ref in files:
            text = self._read_workspace_text(op.from_team, file_ref)
            if text is None:
                errors.append(f"declared API file not found: {file_ref}")
                continue
            for symbol in symbols:
                pattern = rf"\b(def|class)\s+{re.escape(symbol)}\b"
                if not re.search(pattern, text):
                    errors.append(f"symbol {symbol!r} not found in {file_ref}")
        return errors

    def _audit_declare_dependency(self, op: ContractOperation) -> List[str]:
        owner, capability = split_capsule_ref(op.target_ref)
        if not owner:
            owner = op.target_team
        if not owner or not capability:
            return ["declare_dependency target_ref must identify team/capability"]
        cap = self.tool.get_capsule(owner, capability)
        if cap is None:
            return [f"cannot depend on missing capsule: {owner}/{capability}"]
        return []

    def _audit_submit_evidence(self, op: ContractOperation) -> List[str]:
        if not op.related_task_ids and not op.target_ref.startswith("work:"):
            return ["submit_evidence must target a work item or related task"]
        return []

    def _read_workspace_text(self, team_id: str, ref: str) -> str | None:
        if ref.startswith("/workspace/"):
            path = RegistryPath(ref)
        elif ref.startswith("workspace/"):
            path = RegistryPath("/" + ref)
        else:
            path = RegistryPath(f"/workspace/{team_id}/{ref}")
        return self.tool.backend.read_text(path)
