"""Small ContractSpec V8 model for the long-running runtime rewrite."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, Iterable, List


CONTRACT_VERSION = "ContractSpec V8 / Runtime V5"


class ContractValidationError(ValueError):
    """Raised when a generated contract is not schedulable."""


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").replace("\\", "/").strip().strip("/")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


@dataclass
class ProductKernel:
    """Frozen product semantics shared by planner, workers, judges, and repair."""

    ontology: Dict[str, Any] = field(default_factory=dict)
    formulas: Dict[str, Any] = field(default_factory=dict)
    public_api_policy: Dict[str, Any] = field(default_factory=dict)
    test_generation_policy: Dict[str, Any] = field(default_factory=dict)
    schemas: List[Dict[str, Any]] = field(default_factory=list)
    fixtures: List[Dict[str, Any]] = field(default_factory=list)
    flows: List[Dict[str, Any]] = field(default_factory=list)
    invariants: List[Dict[str, Any]] = field(default_factory=list)
    semantic_invariants: List[Dict[str, Any]] = field(default_factory=list)
    acceptance_matrix: List[Dict[str, Any]] = field(default_factory=list)
    public_paths: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "FROZEN"

    def to_record(self) -> Dict[str, Any]:
        return {
            "ontology": dict(self.ontology),
            "formulas": dict(self.formulas),
            "public_api_policy": dict(self.public_api_policy),
            "test_generation_policy": dict(self.test_generation_policy),
            "schemas": list(self.schemas),
            "fixtures": list(self.fixtures),
            "flows": list(self.flows),
            "invariants": list(self.invariants),
            "semantic_invariants": list(self.semantic_invariants),
            "acceptance_matrix": list(self.acceptance_matrix),
            "public_paths": list(self.public_paths),
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "ProductKernel":
        payload = dict(payload or {})
        return cls(
            ontology=dict(payload.get("ontology", {}) or {}),
            formulas=dict(payload.get("formulas", {}) or {}),
            public_api_policy=dict(payload.get("public_api_policy", {}) or {}),
            test_generation_policy=dict(payload.get("test_generation_policy", {}) or {}),
            schemas=list(payload.get("schemas", []) or []),
            fixtures=list(payload.get("fixtures", []) or []),
            flows=list(payload.get("flows", []) or []),
            invariants=list(payload.get("invariants", []) or []),
            semantic_invariants=list(payload.get("semantic_invariants", []) or []),
            acceptance_matrix=list(payload.get("acceptance_matrix", []) or []),
            public_paths=list(payload.get("public_paths", []) or []),
            status=str(payload.get("status", "FROZEN")),
        )


@dataclass
class CanonicalSubstrate:
    """Frozen shared type substrate that must land before dependent slices.

    The substrate is deliberately small: it names the owner artifact for each
    canonical value object/enum and the slice ids that must be implemented
    before consumers are allowed to guess or recreate those shapes.
    """

    owner_by_type: Dict[str, str] = field(default_factory=dict)
    owner_artifacts: List[str] = field(default_factory=list)
    substrate_slice_ids: List[str] = field(default_factory=list)
    consumer_slice_ids: List[str] = field(default_factory=list)
    status: str = "PLANNED"

    def to_record(self) -> Dict[str, Any]:
        return {
            "owner_by_type": dict(self.owner_by_type),
            "owner_artifacts": list(self.owner_artifacts),
            "substrate_slice_ids": list(self.substrate_slice_ids),
            "consumer_slice_ids": list(self.consumer_slice_ids),
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any] | None) -> "CanonicalSubstrate":
        payload = dict(payload or {})
        return cls(
            owner_by_type=dict(payload.get("owner_by_type", {}) or {}),
            owner_artifacts=_dedupe(payload.get("owner_artifacts", []) or []),
            substrate_slice_ids=_dedupe(payload.get("substrate_slice_ids", []) or []),
            consumer_slice_ids=_dedupe(payload.get("consumer_slice_ids", []) or []),
            status=str(payload.get("status", "PLANNED")),
        )


@dataclass
class FeatureSlice:
    id: str
    title: str
    feature_team_id: str = ""
    owner_artifacts: List[str] = field(default_factory=list)
    consumer_artifacts: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    fixture_refs: List[str] = field(default_factory=list)
    invariant_refs: List[str] = field(default_factory=list)
    acceptance_refs: List[str] = field(default_factory=list)
    done_contract: List[str] = field(default_factory=list)
    interface_contract: Dict[str, Any] = field(default_factory=dict)
    semantic_contract: Dict[str, Any] = field(default_factory=dict)
    slice_smoke: List[Dict[str, Any]] = field(default_factory=list)
    phase: str = "slice.build"
    conflict_keys: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "feature_team_id": self.feature_team_id,
            "owner_artifacts": list(self.owner_artifacts),
            "consumer_artifacts": list(self.consumer_artifacts),
            "dependencies": list(self.dependencies),
            "fixture_refs": list(self.fixture_refs),
            "invariant_refs": list(self.invariant_refs),
            "acceptance_refs": list(self.acceptance_refs),
            "done_contract": list(self.done_contract),
            "interface_contract": dict(self.interface_contract),
            "semantic_contract": dict(self.semantic_contract),
            "slice_smoke": list(self.slice_smoke),
            "phase": self.phase,
            "conflict_keys": list(self.conflict_keys),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "FeatureSlice":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            feature_team_id=str(payload.get("feature_team_id", "")).strip(),
            owner_artifacts=_dedupe(payload.get("owner_artifacts", []) or []),
            consumer_artifacts=_dedupe(payload.get("consumer_artifacts", []) or []),
            dependencies=_dedupe(payload.get("dependencies", []) or []),
            fixture_refs=_dedupe(payload.get("fixture_refs", []) or []),
            invariant_refs=_dedupe(payload.get("invariant_refs", []) or []),
            acceptance_refs=_dedupe(payload.get("acceptance_refs", []) or []),
            done_contract=[str(value) for value in payload.get("done_contract", []) or []],
            interface_contract=dict(payload.get("interface_contract", {}) or {}),
            semantic_contract=dict(payload.get("semantic_contract", {}) or {}),
            slice_smoke=list(payload.get("slice_smoke", []) or []),
            phase=str(payload.get("phase", "slice.build")),
            conflict_keys=_dedupe(payload.get("conflict_keys", []) or []),
        )


@dataclass
class InterfaceCapsule:
    """Compact, versioned producer contract shared across async teams.

    Capsules are the only cross-team context workers should rely on. They do
    not freeze private internals; they freeze public modules, canonical imports,
    expected shapes, examples, fixtures, smoke checks, and compatibility rules.
    """

    id: str
    team_id: str
    version: str = "v1"
    producer_slice_ids: List[str] = field(default_factory=list)
    consumer_team_ids: List[str] = field(default_factory=list)
    owner_artifacts: List[str] = field(default_factory=list)
    public_modules: List[str] = field(default_factory=list)
    canonical_imports: Dict[str, str] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    key_signatures: List[Dict[str, Any]] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    fixtures: List[Dict[str, Any]] = field(default_factory=list)
    smoke: List[Dict[str, Any]] = field(default_factory=list)
    contract_tests: List[Dict[str, Any]] = field(default_factory=list)
    lock_item_id: str = ""
    lock_evidence: List[str] = field(default_factory=list)
    compatibility: Dict[str, Any] = field(default_factory=dict)
    status: str = "INTENT"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "team_id": self.team_id,
            "version": self.version,
            "producer_slice_ids": list(self.producer_slice_ids),
            "consumer_team_ids": list(self.consumer_team_ids),
            "owner_artifacts": list(self.owner_artifacts),
            "public_modules": list(self.public_modules),
            "canonical_imports": dict(self.canonical_imports),
            "capabilities": list(self.capabilities),
            "key_signatures": list(self.key_signatures),
            "examples": list(self.examples),
            "fixtures": list(self.fixtures),
            "smoke": list(self.smoke),
            "contract_tests": list(self.contract_tests),
            "lock_item_id": self.lock_item_id,
            "lock_evidence": list(self.lock_evidence),
            "compatibility": dict(self.compatibility),
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "InterfaceCapsule":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            team_id=str(payload.get("team_id", "")).strip(),
            version=str(payload.get("version", "v1")).strip() or "v1",
            producer_slice_ids=_dedupe(payload.get("producer_slice_ids", []) or []),
            consumer_team_ids=_dedupe(payload.get("consumer_team_ids", []) or []),
            owner_artifacts=_dedupe(payload.get("owner_artifacts", []) or []),
            public_modules=_dedupe(payload.get("public_modules", []) or []),
            canonical_imports=dict(payload.get("canonical_imports", {}) or {}),
            capabilities=_dedupe(payload.get("capabilities", []) or []),
            key_signatures=list(payload.get("key_signatures", []) or []),
            examples=list(payload.get("examples", []) or []),
            fixtures=list(payload.get("fixtures", []) or []),
            smoke=list(payload.get("smoke", []) or []),
            contract_tests=list(payload.get("contract_tests", []) or []),
            lock_item_id=str(payload.get("lock_item_id", "")),
            lock_evidence=[str(value) for value in payload.get("lock_evidence", []) or []],
            compatibility=dict(payload.get("compatibility", {}) or {}),
            status=str(payload.get("status", "INTENT")),
        )


@dataclass
class FeatureTeam:
    """Coarse feature-team contract above individual slices."""

    id: str
    title: str
    slice_ids: List[str] = field(default_factory=list)
    owner_artifacts: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    consumer_team_ids: List[str] = field(default_factory=list)
    acceptance_refs: List[str] = field(default_factory=list)
    interface_capsule_refs: List[str] = field(default_factory=list)
    subcontract_ref: str = ""
    local_done_contract: List[str] = field(default_factory=list)
    team_contract: Dict[str, Any] = field(default_factory=dict)
    coordination_mode: str = "mixed"
    status: str = "PLANNED"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "slice_ids": list(self.slice_ids),
            "owner_artifacts": list(self.owner_artifacts),
            "dependencies": list(self.dependencies),
            "consumer_team_ids": list(self.consumer_team_ids),
            "acceptance_refs": list(self.acceptance_refs),
            "interface_capsule_refs": list(self.interface_capsule_refs),
            "subcontract_ref": self.subcontract_ref,
            "local_done_contract": list(self.local_done_contract),
            "team_contract": dict(self.team_contract),
            "coordination_mode": self.coordination_mode,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "FeatureTeam":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            slice_ids=_dedupe(payload.get("slice_ids", []) or []),
            owner_artifacts=_dedupe(payload.get("owner_artifacts", []) or []),
            dependencies=_dedupe(payload.get("dependencies", []) or []),
            consumer_team_ids=_dedupe(payload.get("consumer_team_ids", []) or []),
            acceptance_refs=_dedupe(payload.get("acceptance_refs", []) or []),
            interface_capsule_refs=_dedupe(payload.get("interface_capsule_refs", []) or []),
            subcontract_ref=str(payload.get("subcontract_ref", "")).strip(),
            local_done_contract=[str(value) for value in payload.get("local_done_contract", []) or []],
            team_contract=dict(payload.get("team_contract", {}) or {}),
            coordination_mode=str(payload.get("coordination_mode", "mixed")),
            status=str(payload.get("status", "PLANNED")),
        )


@dataclass
class TeamSubContract:
    """Local contract that keeps a managed team coherent without full graph context."""

    id: str
    team_id: str
    purpose: str
    slice_ids: List[str] = field(default_factory=list)
    owner_artifacts: List[str] = field(default_factory=list)
    owned_concepts: List[str] = field(default_factory=list)
    dependency_team_ids: List[str] = field(default_factory=list)
    dependency_capsule_refs: List[str] = field(default_factory=list)
    interface_capsule_refs: List[str] = field(default_factory=list)
    local_done_contract: List[str] = field(default_factory=list)
    local_quality_gates: List[Dict[str, Any]] = field(default_factory=list)
    internal_parallel_groups: List[List[str]] = field(default_factory=list)
    internal_serial_edges: List[Dict[str, str]] = field(default_factory=list)
    agent_roles: List[Dict[str, Any]] = field(default_factory=list)
    context_policy: Dict[str, Any] = field(default_factory=dict)
    escalation_policy: Dict[str, Any] = field(default_factory=dict)
    status: str = "PLANNED"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "team_id": self.team_id,
            "purpose": self.purpose,
            "slice_ids": list(self.slice_ids),
            "owner_artifacts": list(self.owner_artifacts),
            "owned_concepts": list(self.owned_concepts),
            "dependency_team_ids": list(self.dependency_team_ids),
            "dependency_capsule_refs": list(self.dependency_capsule_refs),
            "interface_capsule_refs": list(self.interface_capsule_refs),
            "local_done_contract": list(self.local_done_contract),
            "local_quality_gates": list(self.local_quality_gates),
            "internal_parallel_groups": [list(group) for group in self.internal_parallel_groups],
            "internal_serial_edges": list(self.internal_serial_edges),
            "agent_roles": list(self.agent_roles),
            "context_policy": dict(self.context_policy),
            "escalation_policy": dict(self.escalation_policy),
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamSubContract":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            team_id=str(payload.get("team_id", "")).strip(),
            purpose=str(payload.get("purpose", "")).strip(),
            slice_ids=_dedupe(payload.get("slice_ids", []) or []),
            owner_artifacts=_dedupe(payload.get("owner_artifacts", []) or []),
            owned_concepts=_dedupe(payload.get("owned_concepts", []) or []),
            dependency_team_ids=_dedupe(payload.get("dependency_team_ids", []) or []),
            dependency_capsule_refs=_dedupe(payload.get("dependency_capsule_refs", []) or []),
            interface_capsule_refs=_dedupe(payload.get("interface_capsule_refs", []) or []),
            local_done_contract=[str(value) for value in payload.get("local_done_contract", []) or []],
            local_quality_gates=list(payload.get("local_quality_gates", []) or []),
            internal_parallel_groups=[
                _dedupe(group) for group in payload.get("internal_parallel_groups", []) or [] if isinstance(group, list)
            ],
            internal_serial_edges=list(payload.get("internal_serial_edges", []) or []),
            agent_roles=list(payload.get("agent_roles", []) or []),
            context_policy=dict(payload.get("context_policy", {}) or {}),
            escalation_policy=dict(payload.get("escalation_policy", {}) or {}),
            status=str(payload.get("status", "PLANNED")),
        )


@dataclass
class AgentSpec:
    id: str
    role: str
    skills: List[str] = field(default_factory=list)
    owns: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {"id": self.id, "role": self.role, "skills": list(self.skills), "owns": list(self.owns)}

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "AgentSpec":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            role=str(payload.get("role", "")).strip(),
            skills=_dedupe(payload.get("skills", []) or []),
            owns=_dedupe(payload.get("owns", []) or []),
        )


@dataclass
class TeamSpec:
    id: str
    slice_id: str
    feature_team_id: str = ""
    slice_ids: List[str] = field(default_factory=list)
    local_contract: Dict[str, Any] = field(default_factory=dict)
    agents: List[AgentSpec] = field(default_factory=list)
    workspace: str = ""
    status: str = "PLANNED"
    phase: str = "slice.build"
    current_item_id: str = ""
    coordination_mode: str = "mixed"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slice_id": self.slice_id,
            "feature_team_id": self.feature_team_id,
            "slice_ids": list(self.slice_ids),
            "local_contract": dict(self.local_contract),
            "agents": [agent.to_record() for agent in self.agents],
            "workspace": self.workspace,
            "status": self.status,
            "phase": self.phase,
            "current_item_id": self.current_item_id,
            "coordination_mode": self.coordination_mode,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamSpec":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            slice_id=str(payload.get("slice_id", "")).strip(),
            feature_team_id=str(payload.get("feature_team_id", "")).strip(),
            slice_ids=_dedupe(payload.get("slice_ids", []) or []),
            local_contract=dict(payload.get("local_contract", {}) or {}),
            agents=[AgentSpec.from_mapping(value) for value in payload.get("agents", []) or []],
            workspace=str(payload.get("workspace", "")),
            status=str(payload.get("status", "PLANNED")),
            phase=str(payload.get("phase", "slice.build")),
            current_item_id=str(payload.get("current_item_id", "")),
            coordination_mode=str(payload.get("coordination_mode", "mixed")),
        )


@dataclass
class TeamStateRecord:
    """Durable async coordination state for one feature team."""

    team_id: str
    phase: str = "planned"
    interface_refs: List[str] = field(default_factory=list)
    frozen_interfaces: List[str] = field(default_factory=list)
    waiting_on_interfaces: List[str] = field(default_factory=list)
    ready_item_ids: List[str] = field(default_factory=list)
    active_item_ids: List[str] = field(default_factory=list)
    mailbox: List[Dict[str, Any]] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "phase": self.phase,
            "interface_refs": list(self.interface_refs),
            "frozen_interfaces": list(self.frozen_interfaces),
            "waiting_on_interfaces": list(self.waiting_on_interfaces),
            "ready_item_ids": list(self.ready_item_ids),
            "active_item_ids": list(self.active_item_ids),
            "mailbox": list(self.mailbox),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamStateRecord":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            phase=str(payload.get("phase", "planned")),
            interface_refs=_dedupe(payload.get("interface_refs", []) or []),
            frozen_interfaces=_dedupe(payload.get("frozen_interfaces", []) or []),
            waiting_on_interfaces=_dedupe(payload.get("waiting_on_interfaces", []) or []),
            ready_item_ids=_dedupe(payload.get("ready_item_ids", []) or []),
            active_item_ids=_dedupe(payload.get("active_item_ids", []) or []),
            mailbox=list(payload.get("mailbox", []) or []),
        )


@dataclass
class WorkItem:
    id: str
    slice_id: str
    title: str
    allowed_artifacts: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    kind: str = "implementation"
    phase: str = "slice.build"
    team_id: str = ""
    feature_team_id: str = ""
    conflict_keys: List[str] = field(default_factory=list)
    locked_artifacts: List[str] = field(default_factory=list)
    repair_transaction_id: str = ""
    status: str = "PENDING"
    attempts: int = 0
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slice_id": self.slice_id,
            "title": self.title,
            "allowed_artifacts": list(self.allowed_artifacts),
            "dependencies": list(self.dependencies),
            "kind": self.kind,
            "phase": self.phase,
            "team_id": self.team_id,
            "feature_team_id": self.feature_team_id,
            "conflict_keys": list(self.conflict_keys),
            "locked_artifacts": list(self.locked_artifacts),
            "repair_transaction_id": self.repair_transaction_id,
            "status": self.status,
            "attempts": self.attempts,
            "diagnostics": list(self.diagnostics),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "WorkItem":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            slice_id=str(payload.get("slice_id", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            allowed_artifacts=_dedupe(payload.get("allowed_artifacts", []) or []),
            dependencies=_dedupe(payload.get("dependencies", []) or []),
            kind=str(payload.get("kind", "implementation")),
            phase=str(payload.get("phase", "slice.build")),
            team_id=str(payload.get("team_id", "")),
            feature_team_id=str(payload.get("feature_team_id", "")),
            conflict_keys=_dedupe(payload.get("conflict_keys", []) or []),
            locked_artifacts=_dedupe(payload.get("locked_artifacts", []) or []),
            repair_transaction_id=str(payload.get("repair_transaction_id", "")),
            status=str(payload.get("status", "PENDING")),
            attempts=int(payload.get("attempts", 0) or 0),
            diagnostics=list(payload.get("diagnostics", []) or []),
            evidence=[str(value) for value in payload.get("evidence", []) or []],
        )


@dataclass
class RepairTransaction:
    id: str
    failure_fingerprint: str
    root_invariant: str
    allowed_artifacts: List[str] = field(default_factory=list)
    locked_tests: List[str] = field(default_factory=list)
    patch_plan: List[str] = field(default_factory=list)
    expected_behavior_delta: str = ""
    validation_commands: List[List[str]] = field(default_factory=list)
    pre_patch_artifact_hashes: Dict[str, str] = field(default_factory=dict)
    last_validation: Dict[str, Any] = field(default_factory=dict)
    status: str = "OPEN"
    attempts: int = 0
    no_progress_count: int = 0
    evidence: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "failure_fingerprint": self.failure_fingerprint,
            "root_invariant": self.root_invariant,
            "allowed_artifacts": list(self.allowed_artifacts),
            "locked_tests": list(self.locked_tests),
            "patch_plan": list(self.patch_plan),
            "expected_behavior_delta": self.expected_behavior_delta,
            "validation_commands": [list(command) for command in self.validation_commands],
            "pre_patch_artifact_hashes": dict(self.pre_patch_artifact_hashes),
            "last_validation": dict(self.last_validation),
            "status": self.status,
            "attempts": self.attempts,
            "no_progress_count": self.no_progress_count,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "RepairTransaction":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")).strip(),
            failure_fingerprint=str(payload.get("failure_fingerprint", "")).strip(),
            root_invariant=str(payload.get("root_invariant", "")).strip(),
            allowed_artifacts=_dedupe(payload.get("allowed_artifacts", []) or []),
            locked_tests=_dedupe(payload.get("locked_tests", []) or []),
            patch_plan=[str(value) for value in payload.get("patch_plan", []) or []],
            expected_behavior_delta=str(payload.get("expected_behavior_delta", "")),
            validation_commands=[
                [str(part) for part in command]
                for command in payload.get("validation_commands", []) or []
                if isinstance(command, list)
            ],
            pre_patch_artifact_hashes=dict(payload.get("pre_patch_artifact_hashes", {}) or {}),
            last_validation=dict(payload.get("last_validation", {}) or {}),
            status=str(payload.get("status", "OPEN")),
            attempts=int(payload.get("attempts", 0) or 0),
            no_progress_count=int(payload.get("no_progress_count", 0) or 0),
            evidence=[str(value) for value in payload.get("evidence", []) or []],
        )


@dataclass
class PromotionRecord:
    id: str
    run_id: str
    slice_id: str
    changed_files: List[str] = field(default_factory=list)
    owned_files: List[str] = field(default_factory=list)
    unowned_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    team_workspace: str = ""
    status: str = "PENDING"
    summary: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "slice_id": self.slice_id,
            "changed_files": list(self.changed_files),
            "owned_files": list(self.owned_files),
            "unowned_files": list(self.unowned_files),
            "missing_files": list(self.missing_files),
            "conflicts": list(self.conflicts),
            "evidence": list(self.evidence),
            "team_workspace": self.team_workspace,
            "status": self.status,
            "summary": self.summary,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "PromotionRecord":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")),
            run_id=str(payload.get("run_id", "")),
            slice_id=str(payload.get("slice_id", "")),
            changed_files=_dedupe(payload.get("changed_files", []) or []),
            owned_files=_dedupe(payload.get("owned_files", []) or []),
            unowned_files=_dedupe(payload.get("unowned_files", []) or []),
            missing_files=_dedupe(payload.get("missing_files", []) or []),
            conflicts=[str(value) for value in payload.get("conflicts", []) or []],
            evidence=[str(value) for value in payload.get("evidence", []) or []],
            team_workspace=str(payload.get("team_workspace", "")),
            status=str(payload.get("status", "PENDING")),
            summary=str(payload.get("summary", "")),
        )


@dataclass
class QualityTransactionRecord:
    """Auditable test+review decision for one slice, repair, or final gate."""

    id: str
    run_id: str
    scope: str
    item_id: str = ""
    slice_id: str = ""
    verdict: str = "PENDING"
    changed_files: List[str] = field(default_factory=list)
    allowed_artifacts: List[str] = field(default_factory=list)
    locked_artifacts: List[str] = field(default_factory=list)
    test_evidence: List[str] = field(default_factory=list)
    review_evidence: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    team_workspace: str = ""
    status: str = "PENDING"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "scope": self.scope,
            "item_id": self.item_id,
            "slice_id": self.slice_id,
            "verdict": self.verdict,
            "changed_files": list(self.changed_files),
            "allowed_artifacts": list(self.allowed_artifacts),
            "locked_artifacts": list(self.locked_artifacts),
            "test_evidence": list(self.test_evidence),
            "review_evidence": list(self.review_evidence),
            "diagnostics": list(self.diagnostics),
            "team_workspace": self.team_workspace,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "QualityTransactionRecord":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")),
            run_id=str(payload.get("run_id", "")),
            scope=str(payload.get("scope", "")),
            item_id=str(payload.get("item_id", "")),
            slice_id=str(payload.get("slice_id", "")),
            verdict=str(payload.get("verdict", "PENDING")),
            changed_files=_dedupe(payload.get("changed_files", []) or []),
            allowed_artifacts=_dedupe(payload.get("allowed_artifacts", []) or []),
            locked_artifacts=_dedupe(payload.get("locked_artifacts", []) or []),
            test_evidence=[str(value) for value in payload.get("test_evidence", []) or []],
            review_evidence=[str(value) for value in payload.get("review_evidence", []) or []],
            diagnostics=list(payload.get("diagnostics", []) or []),
            team_workspace=str(payload.get("team_workspace", "")),
            status=str(payload.get("status", "PENDING")),
        )


@dataclass
class ReplanRecord:
    id: str
    reason: str
    affected_slices: List[str] = field(default_factory=list)
    failure_fingerprint: str = ""
    kernel_delta: Dict[str, Any] = field(default_factory=dict)
    status: str = "OPEN"

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "reason": self.reason,
            "affected_slices": list(self.affected_slices),
            "failure_fingerprint": self.failure_fingerprint,
            "kernel_delta": dict(self.kernel_delta),
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ReplanRecord":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")),
            reason=str(payload.get("reason", "")),
            affected_slices=_dedupe(payload.get("affected_slices", []) or []),
            failure_fingerprint=str(payload.get("failure_fingerprint", "")),
            kernel_delta=dict(payload.get("kernel_delta", {}) or {}),
            status=str(payload.get("status", "OPEN")),
        )


@dataclass
class LLMTelemetry:
    backend: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    tool_iterations: int = 0
    timeouts: int = 0
    errors: int = 0

    def to_record(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tool_calls": self.tool_calls,
            "tool_iterations": self.tool_iterations,
            "timeouts": self.timeouts,
            "errors": self.errors,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "LLMTelemetry":
        payload = dict(payload or {})
        return cls(
            backend=str(payload.get("backend", "")),
            prompt_tokens=int(payload.get("prompt_tokens", 0) or 0),
            completion_tokens=int(payload.get("completion_tokens", 0) or 0),
            tool_calls=int(payload.get("tool_calls", 0) or 0),
            tool_iterations=int(payload.get("tool_iterations", 0) or 0),
            timeouts=int(payload.get("timeouts", 0) or 0),
            errors=int(payload.get("errors", 0) or 0),
        )


@dataclass
class ContractSpec:
    goal: str
    product_kernel: ProductKernel
    feature_slices: List[FeatureSlice]
    work_items: List[WorkItem]
    required_artifacts: List[str]
    canonical_substrate: CanonicalSubstrate = field(default_factory=CanonicalSubstrate)
    test_artifacts: List[str] = field(default_factory=list)
    feature_teams: List[FeatureTeam] = field(default_factory=list)
    team_subcontracts: List[TeamSubContract] = field(default_factory=list)
    interface_capsules: List[InterfaceCapsule] = field(default_factory=list)
    teams: List[TeamSpec] = field(default_factory=list)
    team_states: List[TeamStateRecord] = field(default_factory=list)
    promotions: List[PromotionRecord] = field(default_factory=list)
    quality_transactions: List[QualityTransactionRecord] = field(default_factory=list)
    repair_transactions: List[RepairTransaction] = field(default_factory=list)
    replans: List[ReplanRecord] = field(default_factory=list)
    llm_telemetry: LLMTelemetry = field(default_factory=LLMTelemetry)
    version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not self.goal.strip():
            raise ContractValidationError("contract goal is required")
        if not self.feature_slices:
            raise ContractValidationError("at least one feature slice is required")
        owners: Dict[str, str] = {}
        for feature_slice in self.feature_slices:
            if not feature_slice.id:
                raise ContractValidationError("feature slice id is required")
            for artifact in feature_slice.owner_artifacts:
                previous = owners.get(artifact)
                if previous and previous != feature_slice.id:
                    raise ContractValidationError(f"artifact {artifact} has multiple owners: {previous}, {feature_slice.id}")
                owners[artifact] = feature_slice.id
        missing = [artifact for artifact in self.required_artifacts if artifact not in owners and artifact not in self.test_artifacts]
        if missing:
            raise ContractValidationError(f"required artifacts are not owned by a slice: {missing}")

    def slice_by_id(self) -> Dict[str, FeatureSlice]:
        return {feature_slice.id: feature_slice for feature_slice in self.feature_slices}

    def item_by_id(self) -> Dict[str, WorkItem]:
        return {item.id: item for item in self.work_items}

    def feature_team_by_id(self) -> Dict[str, FeatureTeam]:
        return {team.id: team for team in self.feature_teams}

    def team_subcontract_by_id(self) -> Dict[str, TeamSubContract]:
        return {subcontract.id: subcontract for subcontract in self.team_subcontracts}

    def team_subcontract_by_team_id(self) -> Dict[str, TeamSubContract]:
        return {subcontract.team_id: subcontract for subcontract in self.team_subcontracts}

    def interface_capsule_by_id(self) -> Dict[str, InterfaceCapsule]:
        return {capsule.id: capsule for capsule in self.interface_capsules}

    def interface_capsule_by_team_id(self) -> Dict[str, InterfaceCapsule]:
        return {capsule.team_id: capsule for capsule in self.interface_capsules}

    def to_record(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "goal": self.goal,
            "product_kernel": self.product_kernel.to_record(),
            "canonical_substrate": self.canonical_substrate.to_record(),
            "feature_slices": [feature_slice.to_record() for feature_slice in self.feature_slices],
            "work_items": [item.to_record() for item in self.work_items],
            "required_artifacts": list(self.required_artifacts),
            "test_artifacts": list(self.test_artifacts),
            "feature_teams": [team.to_record() for team in self.feature_teams],
            "team_subcontracts": [subcontract.to_record() for subcontract in self.team_subcontracts],
            "interface_capsules": [capsule.to_record() for capsule in self.interface_capsules],
            "teams": [team.to_record() for team in self.teams],
            "team_states": [state.to_record() for state in self.team_states],
            "promotions": [promotion.to_record() for promotion in self.promotions],
            "quality_transactions": [transaction.to_record() for transaction in self.quality_transactions],
            "repair_transactions": [transaction.to_record() for transaction in self.repair_transactions],
            "replans": [replan.to_record() for replan in self.replans],
            "llm_telemetry": self.llm_telemetry.to_record(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_record(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ContractSpec":
        payload = dict(payload or {})
        contract = cls(
            goal=str(payload.get("goal", "")),
            product_kernel=ProductKernel.from_mapping(payload.get("product_kernel")),
            canonical_substrate=CanonicalSubstrate.from_mapping(payload.get("canonical_substrate")),
            feature_slices=[FeatureSlice.from_mapping(value) for value in payload.get("feature_slices", []) or []],
            work_items=[WorkItem.from_mapping(value) for value in payload.get("work_items", []) or []],
            required_artifacts=_dedupe(payload.get("required_artifacts", []) or []),
            test_artifacts=_dedupe(payload.get("test_artifacts", []) or []),
            feature_teams=[FeatureTeam.from_mapping(value) for value in payload.get("feature_teams", []) or []],
            team_subcontracts=[
                TeamSubContract.from_mapping(value) for value in payload.get("team_subcontracts", []) or []
            ],
            interface_capsules=[
                InterfaceCapsule.from_mapping(value)
                for value in (payload.get("interface_capsules", []) or [])
            ],
            teams=[TeamSpec.from_mapping(value) for value in payload.get("teams", []) or []],
            team_states=[TeamStateRecord.from_mapping(value) for value in payload.get("team_states", []) or []],
            promotions=[PromotionRecord.from_mapping(value) for value in payload.get("promotions", []) or []],
            quality_transactions=[
                QualityTransactionRecord.from_mapping(value)
                for value in payload.get("quality_transactions", []) or []
            ],
            repair_transactions=[
                RepairTransaction.from_mapping(value) for value in payload.get("repair_transactions", []) or []
            ],
            replans=[ReplanRecord.from_mapping(value) for value in payload.get("replans", []) or []],
            llm_telemetry=LLMTelemetry.from_mapping(payload.get("llm_telemetry", {})),
            version=str(payload.get("version", CONTRACT_VERSION)),
        )
        contract.validate()
        return contract
