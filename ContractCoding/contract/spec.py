"""Contract V8 canonical models.

The runtime remains contract-first. V8 keeps the progressive freezing model,
but expresses long coding work as phase contracts: large work moves through a
small serial phase graph while teams and batches run in parallel inside the
active phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from ContractCoding.contract.work_item import WorkItem


CONTRACT_VERSION = "8"
SUPPORTED_CONTRACT_VERSIONS = {CONTRACT_VERSION}
SCOPE_TYPES = {
    "code_module",
    "package",
    "research",
    "doc",
    "ops",
    "data",
    "root",
    "custom",
    "tests",
    "integration",
}


class ContractValidationError(ValueError):
    """Raised when a compiled contract is invalid."""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


INTERFACE_STATUSES = {"DRAFT", "SCAFFOLDED", "TESTED", "FROZEN", "IMPLEMENTED", "VERIFIED"}
TEAM_FREEZE_STATUSES = {
    "DRAFT",
    "READY_FOR_INTERFACE",
    "READY_FOR_BUILD",
    "BUILDING",
    "GATED",
    "PROMOTED",
}
PHASE_MODES = {"serial", "parallel", "hybrid"}
PHASE_STATUSES = {"PLANNED", "ACTIVE", "PASSED", "BLOCKED", "SUPERSEDED"}


def _upper_status(value: str, allowed: set[str], default: str) -> str:
    normalized = str(value or default).strip().upper()
    return normalized if normalized in allowed else default


@dataclass
class WorkScope:
    id: str
    type: str = "custom"
    label: str = ""
    parent_scope: str = ""
    artifacts: List[str] = field(default_factory=list)
    conflict_keys: List[str] = field(default_factory=list)
    execution_plane_policy: str = "auto"
    interfaces: List[Dict[str, Any]] = field(default_factory=list)
    verification_policy: Dict[str, Any] = field(default_factory=dict)
    test_ownership: Dict[str, Any] = field(default_factory=dict)
    team_policy: Dict[str, Any] = field(default_factory=dict)
    promotion_policy: Dict[str, Any] = field(default_factory=dict)
    interface_stability: str = "stable"

    def __post_init__(self) -> None:
        self.id = str(self.id or "root").strip() or "root"
        self.type = str(self.type or "custom").strip().lower()
        if self.type not in SCOPE_TYPES:
            self.type = "custom"
        self.label = str(self.label or self.id).strip() or self.id
        self.parent_scope = str(self.parent_scope or "").strip()
        self.artifacts = [str(value).strip() for value in self.artifacts if str(value).strip()]
        self.conflict_keys = [
            str(value).strip() for value in self.conflict_keys if str(value).strip()
        ]
        self.execution_plane_policy = (
            str(self.execution_plane_policy or "auto").strip().lower() or "auto"
        )
        self.interfaces = [dict(value) for value in self.interfaces if isinstance(value, dict)]
        self.verification_policy = dict(self.verification_policy or {})
        self.test_ownership = dict(self.test_ownership or {})
        self.team_policy = dict(self.team_policy or {})
        self.promotion_policy = dict(self.promotion_policy or {})
        stability = str(self.interface_stability or "stable").strip().lower()
        self.interface_stability = stability if stability in {"stable", "draft", "volatile"} else "stable"

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "WorkScope":
        return cls(
            id=str(payload.get("id", "root")),
            type=str(payload.get("type", "custom")),
            label=str(payload.get("label", "")),
            parent_scope=str(payload.get("parent_scope", "")),
            artifacts=list(payload.get("artifacts", [])),
            conflict_keys=list(payload.get("conflict_keys", [])),
            execution_plane_policy=str(payload.get("execution_plane_policy", "auto")),
            interfaces=list(payload.get("interfaces", [])),
            verification_policy=dict(payload.get("verification_policy", {})),
            test_ownership=dict(payload.get("test_ownership", {})),
            team_policy=dict(payload.get("team_policy", {})),
            promotion_policy=dict(payload.get("promotion_policy", {})),
            interface_stability=str(payload.get("interface_stability", "stable")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
        }
        if self.type != "custom":
            payload["type"] = self.type
        if self.label and self.label != self.id:
            payload["label"] = self.label
        if self.parent_scope and self.parent_scope != "root":
            payload["parent_scope"] = self.parent_scope
        if self.artifacts and self.type not in {"code_module", "tests", "integration", "package"}:
            payload["artifacts"] = list(self.artifacts)
        if self.conflict_keys and not self._conflict_keys_are_derived():
            payload["conflict_keys"] = list(self.conflict_keys)
        if self.execution_plane_policy and self.execution_plane_policy != "auto":
            payload["execution_plane_policy"] = self.execution_plane_policy
        interfaces = [interface for interface in self.interfaces if interface.get("type") != "scope_artifacts"]
        if interfaces:
            payload["interfaces"] = interfaces
        if self.verification_policy and not self._verification_policy_is_derived():
            payload["verification_policy"] = dict(self.verification_policy)
        if self.test_ownership and self.test_ownership.get("owned_tests"):
            payload["test_ownership"] = dict(self.test_ownership)
        if self.team_policy:
            payload["team_policy"] = dict(self.team_policy)
        if self.promotion_policy:
            payload["promotion_policy"] = dict(self.promotion_policy)
        if self.interface_stability != "stable":
            payload["interface_stability"] = self.interface_stability
        return payload

    def _conflict_keys_are_derived(self) -> bool:
        if not self.artifacts:
            return False
        expected = [f"scope:{self.id}", *[f"artifact:{artifact}" for artifact in self.artifacts]]
        return self.conflict_keys == expected

    def _verification_policy_is_derived(self) -> bool:
        expected = {
            "layers": ["self_check", "team_gate"],
            "team_gate_required": True,
            "team_gate_id": f"team:{self.id}",
        }
        if self.type == "integration":
            expected = {"layers": ["final_gate"], "final_gate_id": "final"}
        return self.verification_policy == expected


@dataclass
class TeamGateSpec:
    """Plan-only team gate.

    Team gates are the scope-level quality boundary in ContractSpec V8. They are not
    WorkItems: runtime stores their status separately and only runs them after
    the team's implementation WorkItems pass self-check.
    """

    scope_id: str
    test_artifacts: List[str] = field(default_factory=list)
    test_plan: Dict[str, Any] = field(default_factory=dict)
    review_policy: Dict[str, Any] = field(default_factory=dict)
    deterministic_checks: List[str] = field(default_factory=list)
    required: bool = True

    def __post_init__(self) -> None:
        self.scope_id = str(self.scope_id or "root").strip() or "root"
        self.test_artifacts = [
            str(value).strip() for value in self.test_artifacts if str(value).strip()
        ]
        self.test_plan = dict(self.test_plan or {})
        self.review_policy = dict(self.review_policy or {})
        self.review_policy.setdefault("review_layer", "team")
        self.review_policy.setdefault(
            "allowed_block_reasons",
            [
                "missing_behavior",
                "invalid_tests",
                "interface_mismatch",
                "placeholder",
                "unsafe_side_effect",
            ],
        )
        self.deterministic_checks = [
            str(value).strip() for value in self.deterministic_checks if str(value).strip()
        ] or ["artifact_coverage", "syntax_import", "functional_smoke", "placeholder_scan"]
        self.required = bool(self.required)

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamGateSpec":
        return cls(
            scope_id=str(payload.get("scope_id", "root")),
            test_artifacts=list(payload.get("test_artifacts", [])),
            test_plan=dict(payload.get("test_plan", {})),
            review_policy=dict(payload.get("review_policy", {})),
            deterministic_checks=list(payload.get("deterministic_checks", [])),
            required=bool(payload.get("required", True)),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"scope_id": self.scope_id}
        if self.test_artifacts:
            payload["test_artifacts"] = list(self.test_artifacts)
        if self.test_plan:
            payload["test_plan"] = dict(self.test_plan)
        if self.review_policy:
            payload["review_policy"] = dict(self.review_policy)
        if self.deterministic_checks:
            payload["deterministic_checks"] = list(self.deterministic_checks)
        if not self.required:
            payload["required"] = False
        return payload


@dataclass
class FinalGateSpec:
    """Plan-only project integration gate."""

    required_artifacts: List[str] = field(default_factory=list)
    python_artifacts: List[str] = field(default_factory=list)
    package_roots: List[str] = field(default_factory=list)
    requires_tests: bool = False
    allowed_extra_paths: List[str] = field(default_factory=lambda: ["agent.log"])
    final_acceptance_scenarios: List[Dict[str, Any]] = field(default_factory=list)
    product_behavior: Dict[str, Any] = field(default_factory=dict)
    review_policy: Dict[str, Any] = field(default_factory=dict)
    deterministic_checks: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.required_artifacts = [
            str(value).strip() for value in self.required_artifacts if str(value).strip()
        ]
        self.python_artifacts = [
            str(value).strip() for value in self.python_artifacts if str(value).strip()
        ]
        self.package_roots = [
            str(value).strip() for value in self.package_roots if str(value).strip()
        ]
        self.requires_tests = bool(self.requires_tests)
        self.allowed_extra_paths = [
            str(value).strip() for value in self.allowed_extra_paths if str(value).strip()
        ] or ["agent.log"]
        self.final_acceptance_scenarios = [
            dict(value) for value in self.final_acceptance_scenarios if isinstance(value, dict)
        ]
        self.product_behavior = self._normalize_product_behavior(self.product_behavior)
        self.review_policy = dict(self.review_policy or {})
        self.review_policy.setdefault("review_layer", "final")
        self.review_policy.setdefault(
            "allowed_block_reasons",
            self._default_allowed_block_reasons(),
        )
        self.deterministic_checks = [
            str(value).strip() for value in self.deterministic_checks if str(value).strip()
        ] or self._default_deterministic_checks()

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "FinalGateSpec":
        payload = dict(payload or {})
        return cls(
            required_artifacts=list(payload.get("required_artifacts", [])),
            python_artifacts=list(payload.get("python_artifacts", [])),
            package_roots=list(payload.get("package_roots", [])),
            requires_tests=bool(payload.get("requires_tests", False)),
            allowed_extra_paths=list(payload.get("allowed_extra_paths", ["agent.log"])),
            final_acceptance_scenarios=list(payload.get("final_acceptance_scenarios", [])),
            product_behavior=dict(payload.get("product_behavior") or {}),
            review_policy=dict(payload.get("review_policy", {})),
            deterministic_checks=list(payload.get("deterministic_checks", [])),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.required_artifacts:
            payload["required_artifacts"] = list(self.required_artifacts)
        if self.python_artifacts:
            payload["python_artifacts"] = list(self.python_artifacts)
        if self.package_roots:
            payload["package_roots"] = list(self.package_roots)
        if self.requires_tests:
            payload["requires_tests"] = True
        if self.allowed_extra_paths != ["agent.log"]:
            payload["allowed_extra_paths"] = list(self.allowed_extra_paths)
        if self.final_acceptance_scenarios:
            payload["final_acceptance_scenarios"] = list(self.final_acceptance_scenarios)
        if self.product_behavior:
            payload["product_behavior"] = dict(self.product_behavior)
        if self.review_policy and self.review_policy != self._default_review_policy():
            payload["review_policy"] = dict(self.review_policy)
        if self.deterministic_checks and self.deterministic_checks != self._default_deterministic_checks():
            payload["deterministic_checks"] = list(self.deterministic_checks)
        return payload

    @staticmethod
    def _default_allowed_block_reasons() -> List[str]:
        return [
            "missing_artifact",
            "test_failure",
            "integration_failure",
            "placeholder",
            "unexpected_write",
        ]

    @classmethod
    def _default_review_policy(cls) -> Dict[str, Any]:
        return {
            "review_layer": "final",
            "allowed_block_reasons": cls._default_allowed_block_reasons(),
        }

    @staticmethod
    def _default_deterministic_checks() -> List[str]:
        return [
            "artifact_coverage",
            "compile_import_all",
            "unittest_discover",
            "placeholder_scan",
            "unexpected_writes",
        ]

    @staticmethod
    def _normalize_product_behavior(payload: Dict[str, Any] | None) -> Dict[str, Any]:
        raw = dict(payload or {})
        if not raw:
            return {}
        normalized: Dict[str, Any] = {}
        capabilities = [
            str(value).strip()
            for value in raw.get("capabilities", [])
            if str(value).strip()
        ]
        if capabilities:
            normalized["capabilities"] = capabilities
        scenarios = [
            dict(value)
            for value in raw.get("integration_scenarios", [])
            if isinstance(value, dict)
        ]
        if scenarios:
            normalized["integration_scenarios"] = scenarios
        commands = [
            dict(value)
            for value in raw.get("blackbox_commands", [])
            if isinstance(value, dict)
        ]
        if commands:
            normalized["blackbox_commands"] = commands
        requirements = [
            dict(value)
            for value in raw.get("semantic_requirements", [])
            if isinstance(value, dict)
        ]
        if requirements:
            normalized["semantic_requirements"] = requirements
        targets = dict(raw.get("coverage_targets", {}) or {})
        if targets:
            normalized["coverage_targets"] = targets
        return normalized


@dataclass
class RequirementSpec:
    summary: str = ""
    delivery_type: str = "coding"
    user_flows: List[str] = field(default_factory=list)
    acceptance_scenarios: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)
    quality_bar: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    status: str = "FROZEN"

    def __post_init__(self) -> None:
        self.summary = str(self.summary or "").strip()
        self.delivery_type = str(self.delivery_type or "coding").strip().lower() or "coding"
        self.user_flows = [str(value).strip() for value in self.user_flows if str(value).strip()]
        self.acceptance_scenarios = [
            dict(value) for value in self.acceptance_scenarios if isinstance(value, dict)
        ]
        self.constraints = [str(value).strip() for value in self.constraints if str(value).strip()]
        self.non_goals = [str(value).strip() for value in self.non_goals if str(value).strip()]
        self.quality_bar = [str(value).strip() for value in self.quality_bar if str(value).strip()]
        self.ambiguities = [str(value).strip() for value in self.ambiguities if str(value).strip()]
        self.status = _upper_status(self.status, {"DRAFT", "FROZEN"}, "FROZEN")

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "RequirementSpec":
        payload = dict(payload or {})
        return cls(
            summary=str(payload.get("summary", "")),
            delivery_type=str(payload.get("delivery_type", "coding")),
            user_flows=list(payload.get("user_flows", [])),
            acceptance_scenarios=list(payload.get("acceptance_scenarios", [])),
            constraints=list(payload.get("constraints", [])),
            non_goals=list(payload.get("non_goals", [])),
            quality_bar=list(payload.get("quality_bar", [])),
            ambiguities=list(payload.get("ambiguities", [])),
            status=str(payload.get("status", "FROZEN")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "summary": self.summary,
            "delivery_type": self.delivery_type,
            "status": self.status,
        }
        if self.user_flows:
            payload["user_flows"] = list(self.user_flows)
        if self.acceptance_scenarios:
            payload["acceptance_scenarios"] = list(self.acceptance_scenarios)
        if self.constraints:
            payload["constraints"] = list(self.constraints)
        if self.non_goals:
            payload["non_goals"] = list(self.non_goals)
        if self.quality_bar:
            payload["quality_bar"] = list(self.quality_bar)
        if self.ambiguities:
            payload["ambiguities"] = list(self.ambiguities)
        return payload


@dataclass
class ArchitectureSpec:
    status: str = "DRAFT"
    bounded_contexts: List[Dict[str, Any]] = field(default_factory=list)
    dependency_direction: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, List[str]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = _upper_status(self.status, {"DRAFT", "FROZEN"}, "DRAFT")
        self.bounded_contexts = [
            dict(value) for value in self.bounded_contexts if isinstance(value, dict)
        ]
        self.dependency_direction = [
            dict(value) for value in self.dependency_direction if isinstance(value, dict)
        ]
        self.artifacts = {
            str(key).strip(): [str(item).strip() for item in value if str(item).strip()]
            for key, value in dict(self.artifacts or {}).items()
            if str(key).strip() and isinstance(value, list)
        }
        self.notes = [str(value).strip() for value in self.notes if str(value).strip()]

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "ArchitectureSpec":
        payload = dict(payload or {})
        return cls(
            status=str(payload.get("status", "DRAFT")),
            bounded_contexts=list(payload.get("bounded_contexts", [])),
            dependency_direction=list(payload.get("dependency_direction", [])),
            artifacts=dict(payload.get("artifacts", {})),
            notes=list(payload.get("notes", [])),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": self.status}
        if self.bounded_contexts:
            payload["bounded_contexts"] = list(self.bounded_contexts)
        if self.dependency_direction:
            payload["dependency_direction"] = list(self.dependency_direction)
        if self.artifacts:
            payload["artifacts"] = dict(self.artifacts)
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload


@dataclass
class MilestoneSpec:
    id: str
    mode: str = "serial"
    depends_on: List[str] = field(default_factory=list)
    ready_condition: str = ""
    completion_condition: str = ""
    status: str = "PLANNED"

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.mode = str(self.mode or "serial").strip().lower() or "serial"
        if self.mode not in {"serial", "parallel", "hybrid"}:
            self.mode = "serial"
        self.depends_on = [str(value).strip() for value in self.depends_on if str(value).strip()]
        self.ready_condition = str(self.ready_condition or "").strip()
        self.completion_condition = str(self.completion_condition or "").strip()
        self.status = str(self.status or "PLANNED").strip().upper() or "PLANNED"

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "MilestoneSpec":
        return cls(
            id=str(payload.get("id", "")),
            mode=str(payload.get("mode", "serial")),
            depends_on=list(payload.get("depends_on", [])),
            ready_condition=str(payload.get("ready_condition", "")),
            completion_condition=str(payload.get("completion_condition", "")),
            status=str(payload.get("status", "PLANNED")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"id": self.id, "mode": self.mode, "status": self.status}
        if self.depends_on:
            payload["depends_on"] = list(self.depends_on)
        if self.ready_condition:
            payload["ready_condition"] = self.ready_condition
        if self.completion_condition:
            payload["completion_condition"] = self.completion_condition
        return payload


@dataclass
class PhaseGateSpec:
    checks: List[str] = field(default_factory=list)
    criteria: List[str] = field(default_factory=list)
    evaluator: str = "system"

    def __post_init__(self) -> None:
        self.checks = [str(value).strip() for value in self.checks if str(value).strip()]
        self.criteria = [str(value).strip() for value in self.criteria if str(value).strip()]
        self.evaluator = str(self.evaluator or "system").strip() or "system"

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "PhaseGateSpec":
        payload = dict(payload or {})
        return cls(
            checks=list(payload.get("checks", [])),
            criteria=list(payload.get("criteria", [])),
            evaluator=str(payload.get("evaluator", "system")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"evaluator": self.evaluator}
        if self.checks:
            payload["checks"] = list(self.checks)
        if self.criteria:
            payload["criteria"] = list(self.criteria)
        return payload


@dataclass
class PhaseHandoffSpec:
    artifacts: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.artifacts = [
            str(value).replace("\\", "/").strip()
            for value in self.artifacts
            if str(value).strip()
        ]
        self.notes = [str(value).strip() for value in self.notes if str(value).strip()]

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "PhaseHandoffSpec":
        payload = dict(payload or {})
        return cls(
            artifacts=list(payload.get("artifacts", [])),
            notes=list(payload.get("notes", [])),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.artifacts:
            payload["artifacts"] = list(self.artifacts)
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload


@dataclass
class PhaseContract:
    phase_id: str
    goal: str = ""
    mode: str = "parallel"
    entry_conditions: List[str] = field(default_factory=list)
    teams_in_scope: List[str] = field(default_factory=list)
    deliverables: List[str] = field(default_factory=list)
    phase_gate: PhaseGateSpec | Dict[str, Any] | None = None
    handoff: PhaseHandoffSpec | Dict[str, Any] | None = None
    status: str = "PLANNED"

    def __post_init__(self) -> None:
        self.phase_id = str(self.phase_id or "").strip()
        self.goal = str(self.goal or "").strip()
        self.mode = str(self.mode or "parallel").strip().lower() or "parallel"
        if self.mode not in PHASE_MODES:
            self.mode = "parallel"
        self.entry_conditions = [
            str(value).strip() for value in self.entry_conditions if str(value).strip()
        ]
        self.teams_in_scope = [
            str(value).strip() for value in self.teams_in_scope if str(value).strip()
        ]
        self.deliverables = [
            str(value).replace("\\", "/").strip()
            for value in self.deliverables
            if str(value).strip()
        ]
        if self.phase_gate is None:
            self.phase_gate = PhaseGateSpec()
        elif not isinstance(self.phase_gate, PhaseGateSpec):
            self.phase_gate = PhaseGateSpec.from_mapping(self.phase_gate)
        if self.handoff is None:
            self.handoff = PhaseHandoffSpec()
        elif not isinstance(self.handoff, PhaseHandoffSpec):
            self.handoff = PhaseHandoffSpec.from_mapping(self.handoff)
        self.status = _upper_status(self.status, PHASE_STATUSES, "PLANNED")

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "PhaseContract":
        handoff_payload = payload.get("handoff", None)
        if handoff_payload is None and "handoff_artifacts" in payload:
            handoff_payload = {"artifacts": payload.get("handoff_artifacts", [])}
        return cls(
            phase_id=str(payload.get("phase_id", payload.get("id", ""))),
            goal=str(payload.get("goal", "")),
            mode=str(payload.get("mode", "parallel")),
            entry_conditions=list(payload.get("entry_conditions", [])),
            teams_in_scope=list(payload.get("teams_in_scope", payload.get("teams", []))),
            deliverables=list(payload.get("deliverables", [])),
            phase_gate=payload.get("phase_gate", payload.get("gate", {})),
            handoff=handoff_payload or {},
            status=str(payload.get("status", "PLANNED")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "phase_id": self.phase_id,
            "goal": self.goal,
            "mode": self.mode,
            "status": self.status,
        }
        if self.entry_conditions:
            payload["entry_conditions"] = list(self.entry_conditions)
        if self.teams_in_scope:
            payload["teams_in_scope"] = list(self.teams_in_scope)
        if self.deliverables:
            payload["deliverables"] = list(self.deliverables)
        if isinstance(self.phase_gate, PhaseGateSpec):
            gate = self.phase_gate.to_record()
            if gate.get("checks") or gate.get("criteria"):
                payload["phase_gate"] = gate
        if isinstance(self.handoff, PhaseHandoffSpec):
            handoff = self.handoff.to_record()
            if handoff:
                payload["handoff"] = handoff
        return payload


@dataclass
class InterfaceSpec:
    id: str
    owner_team: str = ""
    consumers: List[str] = field(default_factory=list)
    artifact: str = ""
    symbols: List[Dict[str, Any]] = field(default_factory=list)
    schemas: List[str] = field(default_factory=list)
    semantics: List[str] = field(default_factory=list)
    status: str = "DRAFT"
    critical: bool = False
    source_milestone: str = ""
    stability: str = "team-local"
    scaffold: Dict[str, Any] = field(default_factory=dict)
    conformance_tests: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.owner_team = str(self.owner_team or "").strip()
        self.consumers = [str(value).strip() for value in self.consumers if str(value).strip()]
        self.artifact = str(self.artifact or "").replace("\\", "/").strip()
        self.symbols = [dict(value) for value in self.symbols if isinstance(value, dict)]
        self.schemas = [str(value).strip() for value in self.schemas if str(value).strip()]
        self.semantics = [str(value).strip() for value in self.semantics if str(value).strip()]
        self.status = _upper_status(self.status, INTERFACE_STATUSES, "DRAFT")
        self.critical = bool(self.critical)
        self.source_milestone = str(self.source_milestone or "").strip()
        self.stability = str(self.stability or "team-local").strip().lower() or "team-local"
        self.scaffold = dict(self.scaffold or {})
        self.conformance_tests = [
            str(value).strip() for value in self.conformance_tests if str(value).strip()
        ]

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "InterfaceSpec":
        return cls(
            id=str(payload.get("id", "")),
            owner_team=str(payload.get("owner_team", payload.get("owner", ""))),
            consumers=list(payload.get("consumers", [])),
            artifact=str(payload.get("artifact", "")),
            symbols=list(payload.get("symbols", [])),
            schemas=list(payload.get("schemas", [])),
            semantics=list(payload.get("semantics", [])),
            status=str(payload.get("status", "DRAFT")),
            critical=bool(payload.get("critical", False)),
            source_milestone=str(payload.get("source_milestone", "")),
            stability=str(payload.get("stability", "team-local")),
            scaffold=dict(payload.get("scaffold", {})),
            conformance_tests=list(payload.get("conformance_tests", [])),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
            "owner_team": self.owner_team,
            "status": self.status,
        }
        if self.consumers:
            payload["consumers"] = list(self.consumers)
        if self.artifact:
            payload["artifact"] = self.artifact
        if self.symbols:
            payload["symbols"] = list(self.symbols)
        if self.schemas:
            payload["schemas"] = list(self.schemas)
        if self.semantics:
            payload["semantics"] = list(self.semantics)
        if self.critical:
            payload["critical"] = True
        if self.source_milestone:
            payload["source_milestone"] = self.source_milestone
        if self.stability != "team-local":
            payload["stability"] = self.stability
        if self.scaffold:
            payload["scaffold"] = dict(self.scaffold)
        if self.conformance_tests:
            payload["conformance_tests"] = list(self.conformance_tests)
        return payload


@dataclass
class ContractDelta:
    id: str
    kind: str
    affected_teams: List[str] = field(default_factory=list)
    affected_interfaces: List[str] = field(default_factory=list)
    reason: str = ""
    status: str = "OPEN"

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.kind = str(self.kind or "interface_delta").strip().lower() or "interface_delta"
        self.affected_teams = [
            str(value).strip() for value in self.affected_teams if str(value).strip()
        ]
        self.affected_interfaces = [
            str(value).strip() for value in self.affected_interfaces if str(value).strip()
        ]
        self.reason = str(self.reason or "").strip()
        self.status = str(self.status or "OPEN").strip().upper() or "OPEN"

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ContractDelta":
        return cls(
            id=str(payload.get("id", "")),
            kind=str(payload.get("kind", "interface_delta")),
            affected_teams=list(payload.get("affected_teams", [])),
            affected_interfaces=list(payload.get("affected_interfaces", [])),
            reason=str(payload.get("reason", "")),
            status=str(payload.get("status", "OPEN")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"id": self.id, "kind": self.kind, "status": self.status}
        if self.affected_teams:
            payload["affected_teams"] = list(self.affected_teams)
        if self.affected_interfaces:
            payload["affected_interfaces"] = list(self.affected_interfaces)
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass
class ContractSpec:
    goals: List[str]
    work_scopes: List[WorkScope]
    work_items: List[WorkItem]
    requirements: RequirementSpec | Dict[str, Any] | None = None
    architecture: ArchitectureSpec | Dict[str, Any] | None = None
    milestones: List[MilestoneSpec | Dict[str, Any]] = field(default_factory=list)
    phase_plan: List[PhaseContract | Dict[str, Any]] = field(default_factory=list)
    interfaces: List[InterfaceSpec | Dict[str, Any]] = field(default_factory=list)
    deltas: List[ContractDelta | Dict[str, Any]] = field(default_factory=list)
    team_gates: List[TeamGateSpec] = field(default_factory=list)
    final_gate: Optional[FinalGateSpec] = None
    acceptance_criteria: List[str] = field(default_factory=list)
    execution_policy: Dict[str, Any] = field(default_factory=dict)
    risk_policy: Dict[str, Any] = field(default_factory=dict)
    version: str = CONTRACT_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)
    verification_policy: Dict[str, Any] = field(default_factory=dict)
    test_ownership: Dict[str, Any] = field(default_factory=dict)
    owner_hints: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.version = str(self.version or CONTRACT_VERSION)
        self.goals = [str(value).strip() for value in self.goals if str(value).strip()]
        self.work_scopes = [
            scope if isinstance(scope, WorkScope) else WorkScope.from_mapping(scope)
            for scope in self.work_scopes
        ]
        self.work_items = [
            item if isinstance(item, WorkItem) else WorkItem.from_mapping(item)
            for item in self.work_items
        ]
        if self.requirements is None:
            self.requirements = RequirementSpec(
                summary=self.goals[0] if self.goals else "",
                delivery_type=str(self.metadata.get("delivery_type", "coding") if hasattr(self, "metadata") else "coding"),
            )
        elif not isinstance(self.requirements, RequirementSpec):
            self.requirements = RequirementSpec.from_mapping(self.requirements)
        if self.architecture is None:
            self.architecture = ArchitectureSpec()
        elif not isinstance(self.architecture, ArchitectureSpec):
            self.architecture = ArchitectureSpec.from_mapping(self.architecture)
        self.milestones = [
            milestone if isinstance(milestone, MilestoneSpec) else MilestoneSpec.from_mapping(milestone)
            for milestone in self.milestones
            if isinstance(milestone, (MilestoneSpec, dict))
        ]
        self.phase_plan = [
            phase if isinstance(phase, PhaseContract) else PhaseContract.from_mapping(phase)
            for phase in self.phase_plan
            if isinstance(phase, (PhaseContract, dict))
        ]
        self.interfaces = [
            interface if isinstance(interface, InterfaceSpec) else InterfaceSpec.from_mapping(interface)
            for interface in self.interfaces
            if isinstance(interface, (InterfaceSpec, dict))
        ]
        self.deltas = [
            delta if isinstance(delta, ContractDelta) else ContractDelta.from_mapping(delta)
            for delta in self.deltas
            if isinstance(delta, (ContractDelta, dict))
        ]
        self.team_gates = [
            gate if isinstance(gate, TeamGateSpec) else TeamGateSpec.from_mapping(gate)
            for gate in self.team_gates
        ]
        if self.final_gate is not None and not isinstance(self.final_gate, FinalGateSpec):
            self.final_gate = FinalGateSpec.from_mapping(self.final_gate)
        self.acceptance_criteria = [
            str(value).strip() for value in self.acceptance_criteria if str(value).strip()
        ]
        self.execution_policy = dict(self.execution_policy or {})
        self.risk_policy = dict(self.risk_policy or {})
        self.verification_policy = dict(self.verification_policy or {})
        self.test_ownership = dict(self.test_ownership or {})
        self.owner_hints = {
            str(path).replace("\\", "/").strip(): str(scope).strip()
            for path, scope in dict(self.owner_hints or {}).items()
            if str(path).strip() and str(scope).strip()
        }
        if not self.owner_hints:
            self.owner_hints = self._derive_owner_hints()
        self.metadata = dict(self.metadata or {})

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ContractSpec":
        payload = cls._kernel_payload(payload)
        return cls(
            version=str(payload.get("version", CONTRACT_VERSION)),
            goals=list(payload.get("goals", [])),
            work_scopes=[WorkScope.from_mapping(item) for item in payload.get("work_scopes", [])],
            work_items=[WorkItem.from_mapping(item) for item in payload.get("work_items", [])],
            requirements=RequirementSpec.from_mapping(payload.get("requirements")),
            architecture=ArchitectureSpec.from_mapping(payload.get("architecture")),
            milestones=[MilestoneSpec.from_mapping(item) for item in payload.get("milestones", [])],
            phase_plan=[PhaseContract.from_mapping(item) for item in payload.get("phase_plan", [])],
            interfaces=[InterfaceSpec.from_mapping(item) for item in payload.get("interfaces", [])],
            deltas=[ContractDelta.from_mapping(item) for item in payload.get("deltas", [])],
            team_gates=[TeamGateSpec.from_mapping(item) for item in payload.get("team_gates", [])],
            final_gate=FinalGateSpec.from_mapping(payload.get("final_gate")) if payload.get("final_gate") else None,
            acceptance_criteria=list(payload.get("acceptance_criteria", [])),
            execution_policy=dict(payload.get("execution_policy", {})),
            risk_policy=dict(payload.get("risk_policy", {})),
            verification_policy=dict(payload.get("verification_policy", {})),
            test_ownership=dict(payload.get("test_ownership", {})),
            owner_hints=dict(payload.get("owner_hints", {})),
            metadata=dict(payload.get("metadata", {})),
        )

    @classmethod
    def _kernel_payload(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(payload or {})
        kernel = raw.get("kernel")
        if isinstance(kernel, dict):
            merged = dict(kernel)
            for key in ("requirements", "architecture", "milestones", "interfaces", "deltas"):
                if key in raw and key not in merged:
                    merged[key] = raw[key]
            if "phase_plan" in raw and "phase_plan" not in merged:
                merged["phase_plan"] = raw["phase_plan"]
            return merged
        if any(key in raw for key in ("requirements", "intent", "teams", "work", "gates")) and "work_scopes" not in raw:
            return cls._projection_to_kernel(raw)
        return raw

    @staticmethod
    def _projection_to_kernel(payload: Dict[str, Any]) -> Dict[str, Any]:
        requirements = dict(payload.get("requirements", {}) or {})
        intent = {
            "summary": requirements.get("summary", ""),
            "delivery_type": requirements.get("delivery_type", "coding"),
            "goals": list(payload.get("goals", []) or []),
            "acceptance": list(payload.get("acceptance_criteria", []) or []),
        }
        gates = dict(payload.get("gates", {}) or {})
        policy = dict(payload.get("policy", {}) or {})
        teams = list(payload.get("teams", []) or [])
        work = list(payload.get("work", []) or [])
        goals = list(intent.get("goals", []) or [])
        summary = str(intent.get("summary", "") or "").strip()
        if not goals and summary:
            goals = [summary]
        work_scopes: List[Dict[str, Any]] = [
            {"id": "root", "type": "root", "label": "Root work scope"}
        ]
        for team in teams:
            if not isinstance(team, dict):
                continue
            scope_id = str(team.get("id", "")).strip()
            if not scope_id:
                continue
            work_scopes.append(
                {
                    "id": scope_id,
                    "type": team.get("type") or team.get("kind") or "custom",
                    "label": team.get("label", scope_id),
                    "parent_scope": "root",
                    "artifacts": list(team.get("owns", []) or team.get("artifacts", []) or []),
                    "interfaces": list(team.get("interfaces", []) or []),
                    "interface_stability": team.get("interface_stability", "stable"),
                }
            )
        if gates.get("final"):
            work_scopes.append(
                {
                    "id": "integration",
                    "type": "integration",
                    "label": "System integration gate",
                    "parent_scope": "root",
                }
            )
        work_items: List[Dict[str, Any]] = []
        owner_hints: Dict[str, str] = {}
        for artifact, owner in dict(payload.get("owner_hints", {}) or {}).items():
            normalized = str(artifact).replace("\\", "/").strip()
            scope_id = str(owner).strip()
            if normalized and scope_id:
                owner_hints[normalized] = scope_id
        for team in teams:
            if not isinstance(team, dict):
                continue
            scope_id = str(team.get("id", "")).strip()
            if not scope_id:
                continue
            for artifact in list(team.get("owns", []) or team.get("artifacts", []) or []):
                normalized = str(artifact).replace("\\", "/").strip()
                if normalized:
                    owner_hints[normalized] = scope_id
        for item in work:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "")).strip()
            team_id = str(item.get("team") or item.get("scope_id") or "").strip()
            if not item_id or not team_id:
                continue
            work_items.append(
                {
                    "id": item_id,
                    "kind": item.get("kind", "coding"),
                    "title": item.get("title", item_id),
                    "owner_profile": item.get("owner_profile", "Backend_Engineer"),
                    "scope_id": team_id,
                    "target_artifacts": list(item.get("artifacts", []) or item.get("target_artifacts", []) or []),
                    "acceptance_criteria": list(item.get("acceptance", []) or item.get("acceptance_criteria", []) or []),
                    "depends_on": list(item.get("depends_on", []) or []),
                    "inputs": dict(item.get("inputs", {}) or {}),
                }
            )
            for artifact in list(item.get("artifacts", []) or item.get("target_artifacts", []) or []):
                normalized = str(artifact).replace("\\", "/").strip()
                if normalized and team_id:
                    owner_hints.setdefault(normalized, team_id)
        return {
            "version": str(payload.get("version", CONTRACT_VERSION)),
            "goals": goals,
            "work_scopes": work_scopes,
            "work_items": work_items,
            "team_gates": list(gates.get("team", []) or []),
            "final_gate": gates.get("final"),
            "acceptance_criteria": list(intent.get("acceptance", []) or []),
            "execution_policy": dict(policy.get("execution", {}) or {}),
            "risk_policy": dict(policy.get("risk", {}) or {}),
            "owner_hints": owner_hints,
            "requirements": requirements,
            "architecture": dict(payload.get("architecture", {}) or {}),
            "milestones": list(payload.get("milestones", []) or []),
            "phase_plan": list(payload.get("phase_plan", []) or []),
            "interfaces": list(payload.get("interfaces", []) or []),
            "deltas": list(payload.get("deltas", []) or []),
            "metadata": {
                "task_intent": summary,
                "delivery_type": intent.get("delivery_type", "coding"),
                "architecture": str((payload.get("architecture") or {}).get("name", "milestone-orchestrated-contract-v8")),
            },
        }

    def to_record(self) -> Dict[str, Any]:
        payload = {
            "version": self.version,
            "goals": list(self.goals),
            "requirements": self.requirements.to_record() if isinstance(self.requirements, RequirementSpec) else {},
            "architecture": self.architecture.to_record() if isinstance(self.architecture, ArchitectureSpec) else {},
            "milestones": [milestone.to_record() for milestone in self.milestones],
            "phase_plan": self._public_phase_plan(),
            "interfaces": [interface.to_record() for interface in self.interfaces],
            "work_scopes": [scope.to_record() for scope in self.work_scopes],
            "work_items": [item.to_contract_record() for item in self.work_items],
        }
        if self.deltas:
            payload["deltas"] = [delta.to_record() for delta in self.deltas]
        if self.team_gates:
            payload["team_gates"] = [gate.to_record() for gate in self.team_gates]
        if self.final_gate is not None:
            payload["final_gate"] = self.final_gate.to_record()
        if self.acceptance_criteria:
            payload["acceptance_criteria"] = list(self.acceptance_criteria)
        execution_policy = self._compact_execution_policy()
        if execution_policy:
            payload["execution_policy"] = execution_policy
        risk_policy = dict(self.risk_policy)
        if risk_policy and risk_policy != {"ops_default": "approval_required"}:
            payload["risk_policy"] = risk_policy
        if self.verification_policy:
            payload["verification_policy"] = dict(self.verification_policy)
        if self.test_ownership:
            payload["test_ownership"] = dict(self.test_ownership)
        if self.owner_hints:
            payload["owner_hints"] = dict(sorted(self.owner_hints.items()))
        metadata = self._compact_metadata()
        if metadata:
            payload["metadata"] = metadata
        return payload

    def _derive_owner_hints(self) -> Dict[str, str]:
        hints: Dict[str, str] = {}
        for scope in self.work_scopes:
            if scope.id in {"root", "integration"}:
                continue
            for artifact in scope.artifacts:
                normalized = str(artifact).replace("\\", "/").strip()
                if normalized:
                    hints[normalized] = scope.id
        for item in self.work_items:
            if item.scope_id in {"root", "integration"}:
                continue
            for artifact in item.target_artifacts:
                normalized = str(artifact).replace("\\", "/").strip()
                if normalized and not normalized.startswith(".contractcoding/interfaces/"):
                    hints.setdefault(normalized, item.scope_id)
        return hints

    def to_public_record(self) -> Dict[str, Any]:
        """Return the V8 phase-contract projection written to contract.json."""

        kernel = dict(self.to_record())
        for duplicated in ("requirements", "architecture", "milestones", "phase_plan", "interfaces", "deltas"):
            kernel.pop(duplicated, None)
        return {
            "version": self.version,
            "requirements": self._public_requirements(),
            "architecture": self._public_architecture(),
            "milestones": [milestone.to_record() for milestone in self.milestones],
            "phase_plan": self._public_phase_plan(),
            "teams": self._public_teams(),
            "interfaces": [interface.to_record() for interface in self.interfaces],
            "work": self._public_work(),
            "gates": self._public_gates(),
            "deltas": [delta.to_record() for delta in self.deltas],
            "policy": self._public_policy(),
            "kernel": kernel,
        }

    def _public_requirements(self) -> Dict[str, Any]:
        if isinstance(self.requirements, RequirementSpec):
            return self.requirements.to_record()
        return self._public_intent()

    def _public_architecture(self) -> Dict[str, Any]:
        if isinstance(self.architecture, ArchitectureSpec):
            return self.architecture.to_record()
        return {"status": "DRAFT"}

    def _public_intent(self) -> Dict[str, Any]:
        return {
            "summary": self.metadata.get("task_intent") or (self.goals[0] if self.goals else ""),
            "delivery_type": self.metadata.get("delivery_type", "coding"),
            "goals": list(self.goals),
            **({"acceptance": list(self.acceptance_criteria)} if self.acceptance_criteria else {}),
        }

    def _public_teams(self) -> List[Dict[str, Any]]:
        items_by_scope: Dict[str, List[WorkItem]] = {}
        for item in self.work_items:
            items_by_scope.setdefault(item.scope_id, []).append(item)
        teams: List[Dict[str, Any]] = []
        for scope in self.work_scopes:
            if scope.id in {"root", "integration"}:
                continue
            owns = list(scope.artifacts)
            if not owns:
                owns = [
                    artifact
                    for item in items_by_scope.get(scope.id, [])
                    for artifact in item.target_artifacts
                    if not artifact.startswith(".contractcoding/interfaces/")
                ]
            depends_on = self._team_dependencies(scope.id, items_by_scope)
            payload: Dict[str, Any] = {
                "id": scope.id,
                "type": scope.type,
                "label": scope.label,
                "owns": self._dedupe(owns),
            }
            if depends_on:
                payload["depends_on"] = depends_on
            interfaces = [
                interface
                for interface in scope.interfaces
                if interface.get("type") != "scope_artifacts"
            ]
            if interfaces:
                payload["interfaces"] = interfaces
            if scope.interface_stability != "stable":
                payload["interface_stability"] = scope.interface_stability
            team_state = self._team_freeze_state(scope.id)
            if team_state != "READY_FOR_BUILD":
                payload["freeze_state"] = team_state
            non_default_team_policy = {
                key: value
                for key, value in scope.team_policy.items()
                if key not in {"team_kind", "workspace_plane"} or value not in {"coding", "worktree", "sandbox", "workspace"}
            }
            if non_default_team_policy:
                payload["team_policy"] = non_default_team_policy
            if scope.promotion_policy and scope.promotion_policy != {"mode": "after_team_gate"}:
                payload["promotion_policy"] = dict(scope.promotion_policy)
            teams.append(payload)
        return teams

    def _public_phase_plan(self) -> List[Dict[str, Any]]:
        phases: List[Dict[str, Any]] = []
        for phase in self.phase_plan:
            payload: Dict[str, Any] = {
                "phase_id": phase.phase_id,
                "goal": phase.goal,
                "mode": phase.mode,
                "status": phase.status,
            }
            if phase.entry_conditions:
                payload["entry_conditions"] = list(phase.entry_conditions)
            if phase.teams_in_scope:
                payload["teams_in_scope"] = list(phase.teams_in_scope)
            if phase.deliverables:
                payload["deliverables"] = list(phase.deliverables[:8])
            if isinstance(phase.phase_gate, PhaseGateSpec):
                checks = list(phase.phase_gate.checks[:6])
                if checks:
                    payload["phase_gate"] = {"checks": checks}
            phases.append(payload)
        return phases

    def _team_freeze_state(self, scope_id: str) -> str:
        if scope_id in {"root", "integration"}:
            return "DRAFT"
        interfaces = self.interfaces_for_scope(scope_id)
        if not interfaces:
            return "READY_FOR_BUILD"
        if all(interface.status in {"FROZEN", "IMPLEMENTED", "VERIFIED"} for interface in interfaces):
            return "READY_FOR_BUILD"
        return "READY_FOR_INTERFACE"

    @staticmethod
    def _team_dependencies(scope_id: str, items_by_scope: Dict[str, List[WorkItem]]) -> List[str]:
        dependencies: List[str] = []
        for item in items_by_scope.get(scope_id, []):
            for interface in item.required_interfaces:
                dep_scope = str(interface.get("from_scope") or interface.get("scope") or "").strip()
                if dep_scope and dep_scope != scope_id:
                    dependencies.append(dep_scope)
        return ContractSpec._dedupe(dependencies)

    def _public_work(self) -> List[Dict[str, Any]]:
        work: List[Dict[str, Any]] = []
        for item in self.work_items:
            payload: Dict[str, Any] = {
                "id": item.id,
                "team": item.scope_id,
                "kind": item.kind,
                "title": item.title,
                "artifacts": list(item.target_artifacts),
            }
            if item.depends_on:
                payload["depends_on"] = list(item.depends_on)
            if item.acceptance_criteria:
                payload["acceptance"] = list(item.acceptance_criteria)
            if item.team_role_hint and item.team_role_hint not in {"implementation_worker"}:
                payload["team_role_hint"] = item.team_role_hint
            derived_conflicts = [f"artifact:{artifact}" for artifact in item.target_artifacts]
            if item.conflict_keys and item.conflict_keys != derived_conflicts:
                payload["conflict_keys"] = list(item.conflict_keys)
            if item.serial_group:
                payload["serial_group"] = item.serial_group
            if item.execution_mode and item.execution_mode != "auto":
                payload["execution_mode"] = item.execution_mode
            phase_id = str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")).strip()
            if phase_id:
                payload["phase_id"] = phase_id
            work.append(payload)
        return work

    def _public_gates(self) -> Dict[str, Any]:
        gates: Dict[str, Any] = {}
        if self.team_gates:
            gates["team"] = [self._public_team_gate(gate) for gate in self.team_gates]
        if self.final_gate is not None:
            gates["final"] = self._public_final_gate()
        return gates

    @staticmethod
    def _public_team_gate(gate: TeamGateSpec) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"scope_id": gate.scope_id}
        if gate.test_artifacts:
            payload["test_artifacts"] = list(gate.test_artifacts)
        if gate.test_plan:
            payload["test_plan"] = dict(gate.test_plan)
        if not gate.required:
            payload["required"] = False
        return payload

    def _public_final_gate(self) -> Dict[str, Any]:
        assert self.final_gate is not None
        final = self.final_gate
        payload: Dict[str, Any] = {
            "required_artifacts": list(final.required_artifacts),
            "requires_tests": final.requires_tests,
        }
        if final.package_roots:
            payload["package_roots"] = list(final.package_roots)
        if final.final_acceptance_scenarios:
            payload["final_acceptance_scenarios"] = list(final.final_acceptance_scenarios)
        return payload

    def _public_policy(self) -> Dict[str, Any]:
        policy: Dict[str, Any] = {}
        execution = {
            key: value
            for key, value in self._compact_execution_policy().items()
            if key not in {"context_max_chars", "autonomy_guardrails"}
        }
        if execution:
            policy["execution"] = execution
        risk = dict(self.risk_policy)
        if risk and risk != {"ops_default": "approval_required"}:
            policy["risk"] = risk
        context_max = self.execution_policy.get("context_max_chars")
        if context_max:
            policy.setdefault("context", {})["max_chars"] = context_max
        guardrails = self.execution_policy.get("autonomy_guardrails")
        if guardrails:
            policy.setdefault("recovery", {}).update(dict(guardrails))
        return policy

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        seen = set()
        output: List[str] = []
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(normalized)
        return output

    def _compact_execution_policy(self) -> Dict[str, Any]:
        defaults = {
            "max_parallel_teams": 4,
            "max_parallel_items_per_team": 4,
            "default_execution_plane": "sandbox",
        }
        return {
            key: value
            for key, value in self.execution_policy.items()
            if defaults.get(key) != value
        }

    def _compact_metadata(self) -> Dict[str, Any]:
        if not self.metadata:
            return {}
        keep = {
            "planner",
            "planner_mode",
            "revision",
            "replan_feedback",
            "task_intent",
            "delivery_type",
            "architecture",
            "planning_pipeline",
            "plan_critic",
            "replan_critic",
        }
        metadata = {key: value for key, value in self.metadata.items() if key in keep and value not in (None, "", [], {})}
        for critic_key in ("plan_critic", "replan_critic"):
            if critic_key in metadata and isinstance(metadata[critic_key], dict):
                critic = dict(metadata[critic_key])
                metadata[critic_key] = {
                    "accepted": bool(critic.get("accepted", False)),
                    "context": str(critic.get("context", "")),
                    "errors": list(critic.get("errors", []) or [])[:8],
                    "warnings": list(critic.get("warnings", []) or [])[:8],
                }
        profile = dict(self.metadata.get("planning_profile", {}))
        if profile:
            compact_profile = {
                key: profile[key]
                for key in ("domain", "complexity", "rationale")
                if key in profile and profile[key] not in (None, "", [], {})
            }
            if compact_profile:
                metadata["planning_profile"] = compact_profile
        large_project = dict(self.metadata.get("large_project", {}))
        if large_project:
            metadata["large_project"] = {
                key: large_project[key]
                for key in ("artifact_count", "scope_count", "scope_order")
                if key in large_project
            }
        return metadata

    def to_json(self) -> str:
        return _json_dumps(self.to_public_record()) + "\n"

    def content_hash(self) -> str:
        payload = json.dumps(self.to_record(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def scope_by_id(self) -> Dict[str, WorkScope]:
        return {scope.id: scope for scope in self.work_scopes}

    def item_by_id(self) -> Dict[str, WorkItem]:
        return {item.id: item for item in self.work_items}

    def interfaces_for_scope(self, scope_id: str) -> List[InterfaceSpec]:
        return [
            interface
            for interface in self.interfaces
            if isinstance(interface, InterfaceSpec)
            and interface.owner_team == scope_id
        ]

    def critical_interfaces(self) -> List[InterfaceSpec]:
        return [
            interface
            for interface in self.interfaces
            if isinstance(interface, InterfaceSpec) and interface.critical
        ]

    def team_interface_ready(self, scope_id: str) -> bool:
        interfaces = self.interfaces_for_scope(scope_id)
        if not interfaces:
            return True
        return all(interface.status in self.build_ready_interface_statuses() for interface in interfaces)

    def critical_interfaces_frozen(self) -> bool:
        critical = self.critical_interfaces()
        if not critical:
            return True
        return all(interface.status in self.build_ready_interface_statuses() for interface in critical)

    @staticmethod
    def build_ready_interface_statuses() -> set[str]:
        return {"FROZEN", "IMPLEMENTED", "VERIFIED"}

    def validate(self) -> None:
        if self.version not in SUPPORTED_CONTRACT_VERSIONS:
            raise ContractValidationError(f"Unsupported contract version: {self.version}")
        if not self.goals:
            raise ContractValidationError("Contract must include at least one goal.")
        if not self.work_scopes:
            raise ContractValidationError("Contract must include at least one work scope.")
        if not self.work_items:
            raise ContractValidationError("Contract must include at least one work item.")

        scope_ids = set()
        for scope in self.work_scopes:
            if scope.id in scope_ids:
                raise ContractValidationError(f"Duplicate work scope id: {scope.id}")
            scope_ids.add(scope.id)

        item_ids = set()
        for item in self.work_items:
            if item.id in item_ids:
                raise ContractValidationError(f"Duplicate work item id: {item.id}")
            item_ids.add(item.id)
            if item.scope_id not in scope_ids:
                raise ContractValidationError(
                    f"Work item {item.id} references unknown scope {item.scope_id}."
                )
            if not item.acceptance_criteria and not self.acceptance_criteria:
                raise ContractValidationError(
                    f"Work item {item.id} must include acceptance criteria."
                )

        for item in self.work_items:
            missing = [dependency for dependency in item.depends_on if dependency not in item_ids]
            if missing:
                raise ContractValidationError(
                    f"Work item {item.id} depends on unknown item(s): {', '.join(missing)}"
                )

        gate_scopes = set()
        for gate in self.team_gates:
            if gate.scope_id in gate_scopes:
                raise ContractValidationError(f"Duplicate team gate for scope: {gate.scope_id}")
            gate_scopes.add(gate.scope_id)
            if gate.scope_id not in scope_ids:
                raise ContractValidationError(f"Team gate references unknown scope {gate.scope_id}.")

        interface_ids = set()
        for interface in self.interfaces:
            if not interface.id:
                raise ContractValidationError("Interface spec must include an id.")
            if interface.id in interface_ids:
                raise ContractValidationError(f"Duplicate interface id: {interface.id}")
            interface_ids.add(interface.id)
            if interface.owner_team and interface.owner_team not in scope_ids:
                raise ContractValidationError(
                    f"Interface {interface.id} references unknown owner team {interface.owner_team}."
                )

        phase_ids = set()
        for phase in self.phase_plan:
            if not phase.phase_id:
                raise ContractValidationError("Phase contract must include a phase_id.")
            if phase.phase_id in phase_ids:
                raise ContractValidationError(f"Duplicate phase id: {phase.phase_id}")
            phase_ids.add(phase.phase_id)
            missing_teams = [
                team
                for team in phase.teams_in_scope
                if team not in scope_ids and team not in {"integration", "phase"}
            ]
            if missing_teams:
                raise ContractValidationError(
                    f"Phase {phase.phase_id} references unknown team(s): {', '.join(missing_teams)}"
                )

        self._validate_acyclic(item_ids)

    def _validate_acyclic(self, item_ids: Iterable[str]) -> None:
        graph = {item.id: list(item.depends_on) for item in self.work_items}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item_id: str, path: List[str]) -> None:
            if item_id in visited:
                return
            if item_id in visiting:
                cycle = " -> ".join([*path, item_id])
                raise ContractValidationError(f"Work item dependency cycle detected: {cycle}")
            visiting.add(item_id)
            for dependency in graph.get(item_id, []):
                visit(dependency, [*path, item_id])
            visiting.remove(item_id)
            visited.add(item_id)

        for item_id in item_ids:
            visit(item_id, [])

    def render_markdown(self) -> str:
        public = self.to_public_record()
        requirements = public.get("requirements", {})
        lines = ["# ContractCoding Contract V8", "", f"- Contract hash: `{self.content_hash()}`", ""]
        lines.append("## Requirements")
        lines.append(f"- Status: {requirements.get('status', 'FROZEN')}")
        lines.append(f"- Summary: {requirements.get('summary', '')}")
        lines.append(f"- Delivery type: {requirements.get('delivery_type', 'coding')}")
        if self.goals:
            lines.append("- Goals:")
            for goal in self.goals:
                lines.append(f"  - {goal}")
        if requirements.get("acceptance_scenarios"):
            lines.append("- Acceptance scenarios:")
            for scenario in requirements["acceptance_scenarios"]:
                if isinstance(scenario, dict):
                    lines.append(f"  - {scenario.get('id', 'scenario')}: {scenario.get('description', '')}")
                else:
                    lines.append(f"  - {scenario}")
        lines.append("")
        if public.get("phase_plan"):
            lines.append("## Phase Plan")
            for phase in public.get("phase_plan", []):
                teams = ", ".join(phase.get("teams_in_scope", [])) or "none"
                check = "x" if phase.get("status") == "PASSED" else " "
                lines.append(
                    f"- [{check}] {phase.get('phase_id')}: {phase.get('goal', '')} "
                    f"({phase.get('mode', 'parallel')}; teams: {teams})"
                )
                if phase.get("deliverables"):
                    lines.append(f"  - Deliverables: {', '.join(phase.get('deliverables', [])[:10])}")
                gate = phase.get("phase_gate", {})
                if isinstance(gate, dict) and gate.get("criteria"):
                    lines.append(f"  - Gate: {', '.join(gate.get('criteria', [])[:4])}")
            lines.append("")
        lines.append("## Milestones")
        for milestone in public.get("milestones", []):
            depends = ", ".join(milestone.get("depends_on", [])) or "none"
            lines.append(
                f"- {milestone.get('id')}: {milestone.get('mode', 'serial')} "
                f"({milestone.get('status', 'PLANNED')}, depends: {depends})"
            )
        lines.append("")
        architecture = public.get("architecture", {})
        lines.append("## Architecture")
        lines.append(f"- Status: {architecture.get('status', 'DRAFT')}")
        contexts = architecture.get("bounded_contexts", [])
        if contexts:
            lines.append("- Bounded contexts:")
            for context in contexts:
                if isinstance(context, dict):
                    lines.append(f"  - {context.get('id', '')}: {context.get('label', '')}")
        lines.append("")
        lines.append("## Functional Teams")
        for team in public.get("teams", []):
            lines.append(f"### {team['id']}")
            lines.append(f"- Type: {team.get('type', 'custom')}")
            lines.append(f"- Label: {team.get('label', team['id'])}")
            lines.append(f"- Owns: {', '.join(team.get('owns', [])) or 'None'}")
            if team.get("depends_on"):
                lines.append(f"- Depends on: {', '.join(team['depends_on'])}")
            if team.get("interfaces"):
                lines.append("- Interfaces:")
                for interface in team["interfaces"]:
                    lines.append(f"  - {json.dumps(interface, ensure_ascii=False, sort_keys=True)}")
            if team.get("freeze_state"):
                lines.append(f"- Freeze state: {team['freeze_state']}")
            lines.append("")
        if public.get("interfaces"):
            lines.append("## Frozen Interfaces")
            for interface in public["interfaces"]:
                critical = "critical" if interface.get("critical") else "team-local"
                lines.append(f"### {interface.get('id', '')}")
                lines.append(f"- Owner: {interface.get('owner_team', '')}")
                lines.append(f"- Status: {interface.get('status', 'DRAFT')} ({critical})")
                if interface.get("artifact"):
                    lines.append(f"- Artifact: {interface['artifact']}")
                if interface.get("symbols"):
                    lines.append("- Symbols:")
                    for symbol in interface["symbols"][:8]:
                        lines.append(f"  - {json.dumps(symbol, ensure_ascii=False, sort_keys=True)}")
                lines.append("")
        lines.append("## Work")
        for item in public.get("work", []):
            lines.append(f"### {item['id']}")
            lines.append(f"- Team: {item.get('team', '')}")
            if item.get("phase_id"):
                lines.append(f"- Phase: {item['phase_id']}")
            lines.append(f"- Kind: {item.get('kind', '')}")
            lines.append(f"- Artifacts: {', '.join(item.get('artifacts', [])) or 'None'}")
            if item.get("depends_on"):
                lines.append(f"- Depends on: {', '.join(item['depends_on'])}")
            if item.get("acceptance"):
                lines.append("- Acceptance:")
                for criterion in item["acceptance"]:
                    lines.append(f"  - {criterion}")
            lines.append("")
        gates = public.get("gates", {})
        if gates.get("team"):
            lines.append("## Team Gates")
            for gate in gates["team"]:
                lines.append(f"### team:{gate['scope_id']}")
                lines.append(f"- Test artifacts: {', '.join(gate.get('test_artifacts', [])) or 'None'}")
                plan = gate.get("test_plan", {})
                if plan:
                    lines.append("- Test plan:")
                    for key, value in sorted(plan.items()):
                        lines.append(f"  - {key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
                lines.append("")
        if gates.get("final"):
            final = gates["final"]
            lines.append("## Final Gate")
            lines.append(f"- Required artifacts: {len(final.get('required_artifacts', []))}")
            lines.append(f"- Requires tests: {str(bool(final.get('requires_tests'))).lower()}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_prd_markdown(self) -> str:
        requirements = self._public_requirements()
        lines = ["# PRD Lite", "", f"- Contract hash: `{self.content_hash()}`", ""]
        lines.append(f"## Summary\n{requirements.get('summary', '')}")
        lines.append("")
        lines.append(f"## Delivery Type\n{requirements.get('delivery_type', 'coding')}")
        for title, key in (
            ("User Flows", "user_flows"),
            ("Acceptance Scenarios", "acceptance_scenarios"),
            ("Constraints", "constraints"),
            ("Non Goals", "non_goals"),
            ("Quality Bar", "quality_bar"),
            ("Ambiguities", "ambiguities"),
        ):
            values = requirements.get(key, [])
            if not values:
                continue
            lines.append("")
            lines.append(f"## {title}")
            for value in values:
                if isinstance(value, dict):
                    label = value.get("id") or value.get("name") or "scenario"
                    text = value.get("description") or value.get("summary") or json.dumps(value, ensure_ascii=False, sort_keys=True)
                    lines.append(f"- {label}: {text}")
                else:
                    lines.append(f"- {value}")
        return "\n".join(lines).rstrip() + "\n"


def load_contract_json(text: str) -> ContractSpec:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ContractValidationError("Contract JSON root must be an object.")
    return ContractSpec.from_mapping(payload)


def default_root_scope() -> WorkScope:
    return WorkScope(
        id="root",
        type="root",
        label="Root work scope",
        execution_plane_policy="auto",
    )
