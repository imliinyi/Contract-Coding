"""Generic work-item model for long-running ContractCoding runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from ContractCoding.runtime.fsm import WORK_ITEM_STATUSES, normalize_work_item_status


WORK_ITEM_KINDS = {"coding", "research", "doc", "ops", "data", "eval"}


def normalize_work_item_kind(kind: str | None) -> str:
    value = str(kind or "coding").strip().lower()
    return value if value in WORK_ITEM_KINDS else "coding"


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    return str(value)


@dataclass
class WorkItem:
    """A schedulable unit that is not tied to source files by default."""

    id: str
    kind: str = "coding"
    title: str = ""
    owner_profile: str = "Unknown"
    module: str = "root"
    depends_on: List[str] = field(default_factory=list)
    status: str = "PENDING"
    inputs: Dict[str, Any] = field(default_factory=dict)
    target_artifacts: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    risk_level: str = "medium"
    scope_id: str = "root"
    serial_group: str = ""
    conflict_keys: List[str] = field(default_factory=list)
    execution_mode: str = "auto"
    team_policy: Dict[str, Any] = field(default_factory=dict)
    provided_interfaces: List[Dict[str, Any]] = field(default_factory=list)
    required_interfaces: List[Dict[str, Any]] = field(default_factory=list)
    dependency_policy: str = "done"
    context_policy: Dict[str, Any] = field(default_factory=dict)
    verification_policy: Dict[str, Any] = field(default_factory=dict)
    recovery_policy: Dict[str, Any] = field(default_factory=dict)
    team_role_hint: str = ""

    def __post_init__(self) -> None:
        self.kind = normalize_work_item_kind(self.kind)
        self.status = normalize_work_item_status(self.status)
        self.depends_on = [str(value).strip() for value in self.depends_on if str(value).strip()]
        self.target_artifacts = [
            str(value).strip() for value in self.target_artifacts if str(value).strip()
        ]
        self.acceptance_criteria = [
            str(value).strip() for value in self.acceptance_criteria if str(value).strip()
        ]
        self.evidence = [str(value).strip() for value in self.evidence if str(value).strip()]
        self.scope_id = str(self.scope_id or "root").strip() or "root"
        self.serial_group = str(self.serial_group or "").strip()
        self.conflict_keys = [
            str(value).strip() for value in self.conflict_keys if str(value).strip()
        ]
        self.execution_mode = str(self.execution_mode or "auto").strip().lower() or "auto"
        self.team_policy = dict(self.team_policy or {})
        self.provided_interfaces = [
            dict(value) for value in self.provided_interfaces if isinstance(value, dict)
        ]
        self.required_interfaces = [
            dict(value) for value in self.required_interfaces if isinstance(value, dict)
        ]
        self.context_policy = dict(self.context_policy or {})
        self.verification_policy = dict(self.verification_policy or {})
        self.recovery_policy = dict(self.recovery_policy or {})
        self.team_role_hint = str(self.team_role_hint or "").strip()
        dependency_policy = str(self.dependency_policy or "done").strip().lower()
        self.dependency_policy = dependency_policy if dependency_policy in {"done", "verified", "interface"} else "done"
        if not self.title:
            self.title = self.target_artifacts[0] if self.target_artifacts else self.id

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "WorkItem":
        return cls(
            id=str(payload.get("id", "")).strip(),
            kind=str(payload.get("kind", "coding")),
            title=str(payload.get("title", "")),
            owner_profile=str(payload.get("owner_profile", payload.get("owner", "Unknown"))),
            module=str(payload.get("module", "root")),
            depends_on=list(payload.get("depends_on", [])),
            status=str(payload.get("status", "PENDING")),
            inputs=dict(payload.get("inputs", {})),
            target_artifacts=list(payload.get("target_artifacts", [])),
            acceptance_criteria=list(payload.get("acceptance_criteria", [])),
            evidence=list(payload.get("evidence", [])),
            risk_level=str(payload.get("risk_level", "medium")),
            scope_id=str(payload.get("scope_id", payload.get("module", "root")) or "root"),
            serial_group=str(payload.get("serial_group", "")),
            conflict_keys=list(payload.get("conflict_keys", [])),
            execution_mode=str(payload.get("execution_mode", "auto")),
            team_policy=dict(payload.get("team_policy", {})),
            provided_interfaces=list(payload.get("provided_interfaces", payload.get("interfaces_provided", []))),
            required_interfaces=list(payload.get("required_interfaces", payload.get("interfaces_required", []))),
            dependency_policy=str(payload.get("dependency_policy", "done")),
            context_policy=dict(payload.get("context_policy", {})),
            verification_policy=dict(payload.get("verification_policy", {})),
            recovery_policy=dict(payload.get("recovery_policy", {})),
            team_role_hint=str(payload.get("team_role_hint", "")),
        )

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "owner_profile": self.owner_profile,
            "module": self.module,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "inputs": dict(self.inputs),
            "target_artifacts": list(self.target_artifacts),
            "acceptance_criteria": list(self.acceptance_criteria),
            "evidence": list(self.evidence),
            "risk_level": self.risk_level,
            "scope_id": self.scope_id,
            "serial_group": self.serial_group,
            "conflict_keys": list(self.conflict_keys),
            "execution_mode": self.execution_mode,
            "team_policy": dict(self.team_policy),
            "provided_interfaces": list(self.provided_interfaces),
            "required_interfaces": list(self.required_interfaces),
            "dependency_policy": self.dependency_policy,
            "context_policy": dict(self.context_policy),
            "verification_policy": dict(self.verification_policy),
            "recovery_policy": dict(self.recovery_policy),
            "team_role_hint": self.team_role_hint,
        }

    def to_contract_record(self) -> Dict[str, Any]:
        """Return the plan-only shape written to contract.json.

        Runtime state such as item status and evidence belongs in the run store.
        The contract form is intentionally sparse: default values and fields
        that can be derived from scope or artifact paths are
        materialized by the compiler/runtime after loading.
        """

        payload: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
        }
        if self.title and self.title not in {self.id, self.target_artifacts[0] if self.target_artifacts else ""}:
            payload["title"] = self.title
        if self.owner_profile and self.owner_profile != "Unknown":
            payload["owner_profile"] = self.owner_profile
        if self.module and self.module not in {"root", self.scope_id}:
            payload["module"] = self.module
        if self.depends_on:
            payload["depends_on"] = list(self.depends_on)
        compact_inputs = self._contract_inputs()
        if compact_inputs:
            payload["inputs"] = compact_inputs
        if self.target_artifacts and not self._target_artifacts_are_derived():
            payload["target_artifacts"] = list(self.target_artifacts)
        if self.acceptance_criteria and not self._acceptance_criteria_are_derived():
            payload["acceptance_criteria"] = list(self.acceptance_criteria)
        if self.risk_level and self.risk_level != "medium":
            payload["risk_level"] = self.risk_level
        if self.scope_id and self.scope_id != "root":
            payload["scope_id"] = self.scope_id
        if self.serial_group and not self._serial_group_is_derived():
            payload["serial_group"] = self.serial_group
        if self.conflict_keys and not self._conflict_keys_are_derived():
            payload["conflict_keys"] = list(self.conflict_keys)
        if self.execution_mode and self.execution_mode != "auto" and not self._execution_mode_is_derived():
            payload["execution_mode"] = self.execution_mode
        if self.team_policy:
            payload["team_policy"] = dict(self.team_policy)
        if self.provided_interfaces:
            payload["provided_interfaces"] = list(self.provided_interfaces)
        if self.required_interfaces:
            payload["required_interfaces"] = list(self.required_interfaces)
        if self.dependency_policy and self.dependency_policy != "done":
            payload["dependency_policy"] = self.dependency_policy
        if self.context_policy:
            payload["context_policy"] = dict(self.context_policy)
        verification_policy = self._contract_verification_policy()
        if verification_policy:
            payload["verification_policy"] = verification_policy
        recovery_policy = self._contract_recovery_policy()
        if recovery_policy:
            payload["recovery_policy"] = recovery_policy
        if self.team_role_hint:
            payload["team_role_hint"] = self.team_role_hint
        return payload

    def _contract_inputs(self) -> Dict[str, Any]:
        derived = {
            "goal",
            "auto_planned",
            "large_project",
            "scope_id",
            "scope_artifacts",
            "test_artifacts",
            "python_artifacts",
            "interface_artifacts",
            "required_artifacts",
            "package_roots",
            "requires_tests",
            "allowed_extra_paths",
        }
        out = {
            key: value
            for key, value in self.inputs.items()
            if key not in derived and value not in (None, "", [], {}, False)
        }
        return out

    def _target_artifacts_are_derived(self) -> bool:
        return False

    def _acceptance_criteria_are_derived(self) -> bool:
        return False

    def _serial_group_is_derived(self) -> bool:
        return False

    def _conflict_keys_are_derived(self) -> bool:
        if self.target_artifacts:
            return self.conflict_keys == [f"artifact:{artifact}" for artifact in self.target_artifacts]
        return False

    def _execution_mode_is_derived(self) -> bool:
        return False

    def _contract_verification_policy(self) -> Dict[str, Any]:
        return dict(self.verification_policy)

    def _contract_recovery_policy(self) -> Dict[str, Any]:
        return dict(self.recovery_policy)

    def to_json(self) -> str:
        return json.dumps(self.to_record(), ensure_ascii=False, default=_json_default)

    def is_active(self) -> bool:
        return self.status in {"PENDING", "READY", "RUNNING", "BLOCKED"}

    def is_terminal(self) -> bool:
        return self.status == "VERIFIED"

    def is_ready(self, completed_ids: Iterable[str]) -> bool:
        if self.status not in {"PENDING", "READY", "BLOCKED"}:
            return False
        completed = {str(value) for value in completed_ids}
        return all(dependency in completed for dependency in self.depends_on)

    def with_status(self, status: str, evidence: Optional[Iterable[str]] = None) -> "WorkItem":
        item = WorkItem.from_mapping(self.to_record())
        item.status = normalize_work_item_status(status)
        if evidence:
            item.evidence.extend(str(value).strip() for value in evidence if str(value).strip())
        return item

