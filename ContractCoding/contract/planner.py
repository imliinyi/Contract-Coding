"""Optional LLM draft planning for ContractSpec V8.

The compiled contract remains the source of truth. This planner only proposes a
draft payload; ``ContractCompiler`` owns validation, normalization, gate
materialization, and plan-only hygiene.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Set

from ContractCoding.llm.base import LLMBackend
from ContractCoding.llm.factory import build_backend
from ContractCoding.contract.spec import ContractSpec, ContractValidationError


@dataclass
class DraftPlanResult:
    draft: Optional[Dict[str, Any]]
    backend: str = ""
    error: str = ""
    raw_preview: str = ""

    @property
    def ok(self) -> bool:
        return self.draft is not None and not self.error


@dataclass
class ContractReviewResult:
    accepted: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class PlanCriticResult:
    """Deterministic structural review for compiled contracts.

    The critic is intentionally narrower than ``ContractCompiler``. It catches
    long-running failure modes we have seen in real runs: bad phase placement,
    missing gates, duplicate ownership, and repair-hostile dependencies.
    """

    accepted: bool
    context: str = "plan"
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "context": self.context,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class ContractDraftReviewer:
    """Cheap local guard for untrusted LLM contract drafts.

    The reviewer intentionally avoids becoming a second compiler. It catches
    high-risk shape problems before the deterministic compiler does the full
    normalization and gate synthesis pass.
    """

    REQUIRED_ROOT_FIELDS = ("goals", "work_scopes", "work_items")

    def review(self, draft: Dict[str, Any]) -> ContractReviewResult:
        errors: List[str] = []
        warnings: List[str] = []
        for field_name in self.REQUIRED_ROOT_FIELDS:
            if field_name not in draft:
                errors.append(f"missing required draft field: {field_name}")
        work_scopes = draft.get("work_scopes", [])
        work_items = draft.get("work_items", [])
        if not isinstance(work_scopes, list):
            errors.append("work_scopes must be a list")
            work_scopes = []
        if not isinstance(work_items, list):
            errors.append("work_items must be a list")
            work_items = []
        if not work_items:
            errors.append("draft must include at least one work item")

        scope_ids = {str(scope.get("id", "")) for scope in work_scopes if isinstance(scope, dict)}
        item_ids: Set[str] = set()
        dependency_edges: Dict[str, List[str]] = {}
        for index, item in enumerate(work_items):
            if not isinstance(item, dict):
                errors.append(f"work item at index {index} must be an object")
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                errors.append(f"work item at index {index} is missing id")
                continue
            if item_id in item_ids:
                errors.append(f"duplicate work item id: {item_id}")
            item_ids.add(item_id)
            scope_id = str(item.get("scope_id") or "").strip()
            if not scope_id:
                errors.append(f"work item {item_id} is missing scope_id")
            elif scope_ids and scope_id not in scope_ids:
                errors.append(f"work item {item_id} references unknown scope: {scope_id}")
            if not item.get("acceptance_criteria"):
                errors.append(f"work item {item_id} is missing acceptance_criteria")
            if item.get("status") or item.get("evidence"):
                warnings.append(f"work item {item_id} contains runtime fields that will be discarded")
            depends_on = item.get("depends_on", [])
            if isinstance(depends_on, list):
                dependency_edges[item_id] = [str(dep) for dep in depends_on if dep]
            elif depends_on:
                errors.append(f"work item {item_id} depends_on must be a list")

        cycle = self._dependency_cycle(dependency_edges)
        if cycle:
            errors.append(f"work item dependency cycle: {' -> '.join(cycle)}")
        return ContractReviewResult(accepted=not errors, errors=errors, warnings=warnings)

    @staticmethod
    def _dependency_cycle(edges: Dict[str, List[str]]) -> List[str]:
        visiting: Set[str] = set()
        visited: Set[str] = set()
        stack: List[str] = []

        def visit(node: str) -> Optional[List[str]]:
            if node in visiting:
                try:
                    return stack[stack.index(node) :] + [node]
                except ValueError:
                    return [node, node]
            if node in visited:
                return None
            visiting.add(node)
            stack.append(node)
            for dep in edges.get(node, []):
                if dep in edges:
                    found = visit(dep)
                    if found:
                        return found
            stack.pop()
            visiting.remove(node)
            visited.add(node)
            return None

        for node in edges:
            found = visit(node)
            if found:
                return found
        return []


class ContractDraftPlanner:
    """Ask an LLM for a compact contract draft when explicitly enabled."""

    def __init__(self, backend: Optional[LLMBackend] = None, config: Any = None):
        self.backend = backend
        self.config = config

    @classmethod
    def from_config(cls, config: Any) -> "ContractDraftPlanner":
        return cls(config=config)

    def propose(self, goal: str) -> DraftPlanResult:
        backend = self.backend or build_backend(self.config)
        messages = [
            {
                "role": "system",
                "content": (
                    "You draft ContractSpec V8 contract JSON. The draft is not trusted: a deterministic "
                    "compiler will validate and rewrite it. Return only JSON inside <contract_draft> tags. "
                    "Do not include runtime status, evidence, leases, attempts, or events. Prefer concise "
                    "scopes, stable interfaces, acceptance criteria, conflict keys, and verification gates. "
                    "Kinds allowed: coding, research, doc, data, ops, eval."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Draft a ContractCoding plan for this task. Keep it compact and schedulable.\n\n"
                    f"Task:\n{goal}"
                ),
            },
        ]
        try:
            response = backend.chat(messages)
        except Exception as exc:
            return DraftPlanResult(draft=None, backend=getattr(backend, "name", ""), error=str(exc))

        raw = response.content or ""
        try:
            payload = self.extract_draft(raw)
        except ValueError as exc:
            return DraftPlanResult(
                draft=None,
                backend=response.backend,
                error=str(exc),
                raw_preview=raw[:1000],
            )
        return DraftPlanResult(
            draft=payload,
            backend=response.backend,
            raw_preview=raw[:1000],
        )

    @staticmethod
    def extract_draft(text: str) -> Dict[str, Any]:
        if not text:
            raise ValueError("LLM planner returned an empty draft.")
        match = re.search(r"<contract_draft>\s*(.*?)\s*</contract_draft>", text, re.DOTALL)
        raw = match.group(1) if match else text.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("LLM planner draft did not contain a JSON object.")
        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM planner draft JSON is invalid: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM planner draft root must be a JSON object.")
        return payload


class PlanCritic:
    """Cheap planner/replanner guard for compiled ContractSpec objects."""

    HIGH_RISK_SCOPE_IDS = {"interface", "integration", "tests"}
    STRONG_CODING_MARKERS = (
        "python package",
        "dependency-free python",
        "package named",
        "package called",
        "unittest",
        "test suite",
        "compile/import",
        "compile import",
        "cli ",
        " cli",
        "source code",
        "modules",
    )

    def review_contract(self, contract: ContractSpec, *, context: str = "plan") -> PlanCriticResult:
        errors: List[str] = []
        warnings: List[str] = []
        try:
            contract.validate()
        except ContractValidationError as exc:
            errors.append(str(exc))

        item_by_id = contract.item_by_id()
        scope_ids = {scope.id for scope in contract.work_scopes}
        phase_ids = {phase.phase_id for phase in contract.phase_plan}
        goal_text = " ".join(contract.goals + [getattr(contract.requirements, "summary", "")]).lower()
        requirements_delivery = str(getattr(contract.requirements, "delivery_type", "") or "").lower()
        metadata_delivery = str(contract.metadata.get("delivery_type", "") or "").lower()
        strong_coding_intent = self._strong_coding_intent(goal_text)
        coding_items = [item for item in contract.work_items if item.kind == "coding"]
        python_artifacts = [
            artifact
            for item in coding_items
            for artifact in item.target_artifacts
            if artifact.endswith(".py")
        ]
        if requirements_delivery == "coding" and metadata_delivery and metadata_delivery != "coding":
            errors.append(
                f"requirements delivery_type is coding but compiled metadata delivery_type is {metadata_delivery}"
            )
        if strong_coding_intent and not coding_items:
            errors.append("strong coding/package intent produced no coding work items")
        if strong_coding_intent and contract.final_gate is not None:
            required = list(contract.final_gate.required_artifacts)
            hidden_only = bool(required) and all(path.startswith(".contractcoding/") for path in required)
            if hidden_only:
                errors.append("strong coding/package intent final gate only requires .contractcoding artifacts")
            if not (contract.final_gate.python_artifacts or python_artifacts):
                errors.append("strong coding/package intent has no Python artifacts for compile/import verification")
        dependency_edges = {item.id: list(item.depends_on) for item in contract.work_items}
        cycle = ContractDraftReviewer._dependency_cycle(dependency_edges)
        if cycle:
            errors.append(f"work item dependency cycle: {' -> '.join(cycle)}")

        owned_artifacts: Dict[str, List[str]] = {}
        for item in contract.work_items:
            if item.scope_id not in scope_ids:
                errors.append(f"work item {item.id} references unknown scope: {item.scope_id}")
            missing_dependencies = [dependency for dependency in item.depends_on if dependency not in item_by_id]
            if missing_dependencies:
                errors.append(
                    f"work item {item.id} depends on unknown item(s): {', '.join(missing_dependencies)}"
                )
            phase_id = str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")).strip()
            if phase_id and phase_ids and phase_id not in phase_ids:
                errors.append(f"work item {item.id} references unknown phase_id: {phase_id}")
            if item.kind == "coding" and not item.target_artifacts:
                warnings.append(f"coding work item {item.id} has no target_artifacts")
            if item.kind == "coding" and item.scope_id == "interface" and phase_id == "vertical_slice":
                errors.append(
                    f"interface work item {item.id} is in vertical_slice; public entrypoints should run after owner scopes"
                )
            if item.kind == "coding":
                for artifact in item.target_artifacts:
                    normalized = self._normalize_path(artifact)
                    if not normalized or normalized.startswith(".contractcoding/"):
                        continue
                    owned_artifacts.setdefault(normalized, []).append(item.id)

        for artifact, owners in sorted(owned_artifacts.items()):
            unique = list(dict.fromkeys(owners))
            if len(unique) > 1:
                warnings.append(f"artifact {artifact} is targeted by multiple work items: {', '.join(unique)}")

        gate_scope_ids = {gate.scope_id for gate in contract.team_gates}
        implementation_scopes = {
            item.scope_id
            for item in contract.work_items
            if item.kind == "coding"
            and item.scope_id not in self.HIGH_RISK_SCOPE_IDS
            and any(not artifact.startswith(".contractcoding/") for artifact in item.target_artifacts)
        }
        for scope_id in sorted(implementation_scopes):
            if scope_id not in gate_scope_ids:
                warnings.append(f"implementation scope {scope_id} has no team gate")
        if implementation_scopes and contract.final_gate is None:
            warnings.append("contract has implementation scopes but no final gate")

        for gate in contract.team_gates:
            test_plan = dict(gate.test_plan or {})
            if gate.test_artifacts and not test_plan.get("test_strata"):
                warnings.append(f"team gate {gate.scope_id} has test artifacts without test_strata")
            if gate.scope_id == "interface":
                dependencies = list(test_plan.get("dependency_scope_ids", []) or [])
                if implementation_scopes and not dependencies:
                    errors.append("interface team gate must declare dependency_scope_ids for upstream owner scopes")
            for dependency_scope in test_plan.get("dependency_scope_ids", []) or []:
                if dependency_scope not in scope_ids:
                    errors.append(f"team gate {gate.scope_id} references unknown dependency scope {dependency_scope}")

        return PlanCriticResult(
            accepted=not errors,
            context=context,
            errors=list(dict.fromkeys(errors)),
            warnings=list(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        return str(path or "").replace("\\", "/").strip().strip("/")

    @classmethod
    def _strong_coding_intent(cls, text: str) -> bool:
        haystack = f" {str(text or '').lower()} "
        return any(marker in haystack for marker in cls.STRONG_CODING_MARKERS)
