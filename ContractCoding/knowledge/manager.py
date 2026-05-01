"""Knowledge layer: context packets, memory compression, and skill adaptation."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Sequence

from ContractCoding.config import Config
from ContractCoding.contract.spec import ContractSpec, WorkScope
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.llm.base import LLMBackend
from ContractCoding.llm.factory import build_backend
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


@dataclass
class ContextBudget:
    max_messages: int = 5
    max_chars: int = 24000
    skill_chars: int = 4000


@dataclass
class SkillSpec:
    name: str
    description: str = ""
    prompt: str = ""
    path: str = ""
    allowed_work_kinds: List[str] = field(default_factory=lambda: ["*"])
    trigger_keywords: List[str] = field(default_factory=list)
    output_schema: Dict[str, str] = field(default_factory=dict)
    evidence_requirements: List[str] = field(default_factory=list)
    tool_hints: List[str] = field(default_factory=list)
    risk_policy: str = ""
    priority: int = 50

    def accepts(self, work_kind: str) -> bool:
        normalized = (work_kind or "").strip().lower()
        allowed = {kind.strip().lower() for kind in self.allowed_work_kinds}
        return "*" in allowed or normalized in allowed

    def render(self) -> str:
        parts = [f"### {self.name}"]
        if self.description:
            parts.append(self.description.strip())
        if self.path:
            parts.append(f"Source: {self.path}")
        if self.trigger_keywords:
            parts.append("Triggers: " + ", ".join(self.trigger_keywords))
        if self.output_schema:
            schema = "; ".join(f"{key}: {value}" for key, value in self.output_schema.items())
            parts.append("Output schema: " + schema)
        if self.evidence_requirements:
            parts.append("Evidence required: " + "; ".join(self.evidence_requirements))
        if self.tool_hints:
            parts.append("Tool hints: " + ", ".join(self.tool_hints))
        if self.risk_policy:
            parts.append("Risk policy: " + self.risk_policy)
        if self.prompt:
            parts.append(self.prompt.strip())
        return "\n".join(part for part in parts if part)


@dataclass
class SkillSelection:
    skills: List[SkillSpec] = field(default_factory=list)
    selected_names: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    worker_protocol: List[str] = field(default_factory=list)
    locked_artifacts: List[str] = field(default_factory=list)
    repair_packet: Dict[str, Any] = field(default_factory=dict)
    reasons: Dict[str, str] = field(default_factory=dict)


class SkillRouter:
    """Select the smallest useful skill/tool packet for a WorkItem."""

    DEFAULT_CODING_TOOLS = [
        "file_tree",
        "search_text",
        "read_lines",
        "inspect_symbol",
        "create_file",
        "replace_file",
        "write_file",
        "update_file_lines",
        "replace_symbol",
        "report_blocker",
        "submit_result",
    ]
    REPAIR_TOOLS = [
        "search_text",
        "read_lines",
        "inspect_symbol",
        "replace_file",
        "update_file_lines",
        "replace_symbol",
        "report_blocker",
        "submit_result",
    ]
    READ_ONLY_TOOLS = ["file_tree", "read_file", "search_text", "report_blocker", "submit_result"]

    def select(
        self,
        *,
        skills: Sequence[SkillSpec],
        item: WorkItem,
        scope: WorkScope,
        contract: ContractSpec,
        diagnostics: List[Dict[str, Any]],
    ) -> SkillSelection:
        repair_packet = self._repair_packet(item, diagnostics)
        locked_artifacts = list(repair_packet.get("locked_artifacts", []))
        haystack = self._haystack(item, scope, contract, diagnostics, repair_packet)
        is_test_generation_item = self._is_test_generation_item(item)
        is_gate_review_item = item.kind == "eval" or item.id.startswith("gate:") or "review" in haystack
        has_final_diagnostics = bool(diagnostics) and (
            scope.id == "integration" or item.scope_id == "integration" or any("final" in str(d).lower() for d in diagnostics)
        )
        scores: Dict[str, int] = {}
        reasons: Dict[str, str] = {}
        for skill in skills:
            if not skill.accepts(item.kind):
                continue
            if skill.name == "coding.test_generation_workflow" and not is_test_generation_item:
                continue
            if skill.name == "coding.code_generation_workflow" and is_test_generation_item:
                continue
            if skill.name == "coding.review_gate_workflow" and not is_gate_review_item:
                continue
            if skill.name in {"coding.repair", "coding.integration_repair"} and not diagnostics:
                continue
            if skill.name == "coding.final_recovery" and not (has_final_diagnostics or item.id.startswith("gate:")):
                continue
            score = max(0, 60 - int(skill.priority))
            matched = [keyword for keyword in skill.trigger_keywords if keyword.lower() in haystack]
            score += len(matched) * 10
            if skill.name == "general.delivery":
                score += 100
            if item.kind == "coding" and skill.name == "coding.implementation":
                score += 80
            if item.kind == "coding" and skill.name == "coding.code_generation_workflow":
                score += 95
            if item.kind == "coding" and skill.name == "coding.test_generation_workflow":
                score += 130
            if skill.name == "coding.review_gate_workflow" and (
                item.kind == "eval" or "review" in haystack or "gate" in haystack
            ):
                score += 95
            if diagnostics and skill.name == "coding.repair":
                score += 90
            if diagnostics and (scope.id == "integration" or any("final" in str(d).lower() for d in diagnostics)):
                if skill.name == "coding.integration_repair":
                    score += 95
                if skill.name == "coding.final_recovery":
                    score += 110
            if self._is_public_entrypoint_item(item) and skill.name == "coding.public_entrypoint_import_safety":
                score += 85
            if self._is_large_project(contract, item) and skill.name == "coding.large_project_generation":
                score += 65
            phase_id = str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")).lower()
            if phase_id and skill.name == "coding.phase_contract":
                score += 70
            if phase_id == "vertical_slice" and skill.name == "coding.vertical_slice":
                score += 70
            if item.kind == "eval" and skill.name == "coding.evaluator_gate":
                score += 75
            if score > 0:
                scores[skill.name] = score
                reasons[skill.name] = ", ".join(matched[:4]) or "work-item fit"

        selected = [
            skill
            for skill in skills
            if skill.name in scores
        ]
        selected.sort(key=lambda skill: (-scores.get(skill.name, 0), skill.priority, skill.name))
        if item.kind == "coding":
            selected = self._ensure_skill(selected, skills, "general.delivery")
            selected = self._ensure_skill(selected, skills, "coding.implementation")
            if self._is_test_generation_item(item):
                selected = self._ensure_skill(selected, skills, "coding.test_generation_workflow")
            else:
                selected = self._ensure_skill(selected, skills, "coding.code_generation_workflow")
        else:
            selected = self._ensure_skill(selected, skills, "general.delivery")
        if item.kind == "eval":
            selected = self._ensure_skill(selected, skills, "coding.review_gate_workflow")
        selected = selected[:7]
        selected.sort(key=lambda skill: (skill.priority, skill.name))

        allowed_tools = self._allowed_tools(selected, item, diagnostics)
        protocol = self._worker_protocol(item, diagnostics, locked_artifacts)
        return SkillSelection(
            skills=selected,
            selected_names=[skill.name for skill in selected],
            allowed_tools=allowed_tools,
            worker_protocol=protocol,
            locked_artifacts=locked_artifacts,
            repair_packet=repair_packet,
            reasons=reasons,
        )

    def render(self, selection: SkillSelection, max_chars: int) -> str:
        rendered = [skill.render() for skill in selection.skills]
        return ContextManager._fit_text("\n\n".join(rendered), max_chars)

    @classmethod
    def _ensure_skill(
        cls,
        selected: List[SkillSpec],
        skills: Sequence[SkillSpec],
        name: str,
    ) -> List[SkillSpec]:
        if any(skill.name == name for skill in selected):
            return selected
        match = next((skill for skill in skills if skill.name == name), None)
        return [match, *selected] if match is not None else selected

    @classmethod
    def _allowed_tools(
        cls,
        selected: Sequence[SkillSpec],
        item: WorkItem,
        diagnostics: List[Dict[str, Any]],
    ) -> List[str]:
        tools: List[str] = []
        if item.kind == "coding":
            tools.extend(cls.REPAIR_TOOLS if diagnostics else cls.DEFAULT_CODING_TOOLS)
        elif item.kind in {"research", "doc", "data"}:
            tools.extend(cls.READ_ONLY_TOOLS)
        elif item.kind == "eval":
            tools.extend(["read_file", "search_text", "run_code", "report_blocker", "submit_result"])
        for skill in selected:
            tools.extend(skill.tool_hints)
        if any(
            str(diagnostic.get("failure_kind", "")).lower() == "missing_artifact"
            for diagnostic in diagnostics
        ) and "create_file" not in tools:
            tools.append("create_file")
        return [tool for tool in dict.fromkeys(tools) if tool]

    @classmethod
    def _worker_protocol(
        cls,
        item: WorkItem,
        diagnostics: List[Dict[str, Any]],
        locked_artifacts: List[str],
    ) -> List[str]:
        protocol = [
            "inspect_existing_context_before_editing",
            "write_only_allowed_artifacts_or_report_blocker",
            "prefer_narrow_symbol_or_line_edits_after_reading_context",
            "finish_with_changed_files_and_evidence",
        ]
        if item.kind == "coding":
            protocol.append("use_replace_file_for_scaffold_or_whole_file_replacement")
        if diagnostics:
            protocol.insert(0, "repair_ticket_first_resolve_the_structured_diagnostic")
            protocol.append("do_not_repeat_a_rolled_back_patch")
        if locked_artifacts:
            protocol.append("locked_artifacts_are_read_only")
        if cls._is_public_entrypoint_item(item):
            protocol.append("keep_public_entrypoints_import_safe_with_lazy_downstream_imports")
        return protocol

    @classmethod
    def _repair_packet(cls, item: WorkItem, diagnostics: List[Dict[str, Any]]) -> Dict[str, Any]:
        existing = item.inputs.get("repair_packet")
        if isinstance(existing, dict) and existing:
            return dict(existing)
        if not diagnostics and not item.inputs.get("repair_ticket_id"):
            return {}
        editable_tests = cls._work_item_targets_tests(item) or any(cls._diagnostic_allows_test_edit(d) for d in diagnostics)
        test_artifacts = cls._diagnostic_test_artifacts(diagnostics)
        locked_artifacts = [] if editable_tests else test_artifacts
        return {
            "protocol_version": "2",
            "repair_ticket_id": str(item.inputs.get("repair_ticket_id", "") or ""),
            "repair_mode": str(item.inputs.get("repair_mode", "") or ""),
            "owner_scope": item.scope_id,
            "allowed_artifacts": list(item.target_artifacts),
            "locked_artifacts": locked_artifacts,
            "editable_tests": editable_tests,
            "diagnostics": diagnostics[:5],
            "instruction": str(item.inputs.get("repair_instruction", "") or ""),
        }

    @staticmethod
    def _diagnostic_allows_test_edit(diagnostic: Dict[str, Any]) -> bool:
        action = str(diagnostic.get("recovery_action", "") or "").lower()
        kind = str(diagnostic.get("failure_kind", "") or "").lower()
        return kind == "invalid_tests" or action in {"test_repair", "test_regeneration"}

    @classmethod
    def _diagnostic_test_artifacts(cls, diagnostics: List[Dict[str, Any]]) -> List[str]:
        artifacts: List[str] = []
        for diagnostic in diagnostics:
            for key in ("test_artifacts", "affected_artifacts", "external_artifacts"):
                for artifact in diagnostic.get(key, []) or []:
                    normalized = str(artifact).replace("\\", "/").strip()
                    if normalized and cls._is_test_artifact(normalized) and normalized not in artifacts:
                        artifacts.append(normalized)
        return artifacts

    @staticmethod
    def _haystack(
        item: WorkItem,
        scope: WorkScope,
        contract: ContractSpec,
        diagnostics: List[Dict[str, Any]],
        repair_packet: Dict[str, Any],
    ) -> str:
        pieces: List[str] = [
            item.id,
            item.kind,
            item.title,
            item.owner_profile,
            item.team_role_hint,
            item.scope_id,
            scope.id,
            scope.type,
            " ".join(item.target_artifacts),
            " ".join(item.acceptance_criteria),
            " ".join(str(interface) for interface in item.provided_interfaces + item.required_interfaces),
            str(item.inputs),
            str(item.verification_policy),
            " ".join(str(phase.phase_id) for phase in contract.phase_plan),
            str(repair_packet),
            str(diagnostics),
        ]
        return " ".join(pieces).lower()

    @staticmethod
    def _is_large_project(contract: ContractSpec, item: WorkItem) -> bool:
        large = dict(contract.metadata.get("large_project", {}))
        artifact_count = int(large.get("artifact_count", 0) or 0)
        return artifact_count >= 12 or len(contract.work_items) >= 12 or len(item.target_artifacts) >= 4

    @staticmethod
    def _is_public_entrypoint_item(item: WorkItem) -> bool:
        text = " ".join([item.id, item.title, *item.target_artifacts]).lower()
        return any(marker in text for marker in ("cli", "api", "repl", "entrypoint", "__main__", "main.py"))

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    @classmethod
    def _work_item_targets_tests(cls, item: WorkItem) -> bool:
        return any(cls._is_test_artifact(path) for path in item.target_artifacts)

    @classmethod
    def _is_test_generation_item(cls, item: WorkItem) -> bool:
        text = " ".join(
            [
                item.id,
                item.title,
                item.owner_profile,
                item.team_role_hint,
                item.scope_id,
                " ".join(item.target_artifacts),
                " ".join(item.acceptance_criteria),
                str(item.inputs),
                str(item.verification_policy),
            ]
        ).lower()
        return (
            "test_engineer" in text
            or "test_regeneration" in text
            or "missing_test_artifact" in text
            or "invalid_tests" in text
            or cls._work_item_targets_tests(item)
        )


@dataclass
class AgentInputPacket:
    """Small contract slice handed to a single agent step."""

    packet_version: str
    task: str
    wave_kind: str
    contract_hash: str
    work_item: Dict[str, Any]
    scope: Dict[str, Any]
    phase: Dict[str, Any] = field(default_factory=dict)
    team_brief: Dict[str, Any] = field(default_factory=dict)
    agent_role_hint: str = ""
    verification_layer: str = ""
    scope_test_hints: Dict[str, Any] = field(default_factory=dict)
    direct_dependencies: List[Dict[str, Any]] = field(default_factory=list)
    allowed_artifacts: List[str] = field(default_factory=list)
    conflict_keys: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    recent_evidence: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    selected_skills: List[str] = field(default_factory=list)
    worker_protocol: List[str] = field(default_factory=list)
    locked_artifacts: List[str] = field(default_factory=list)
    repair_packet: Dict[str, Any] = field(default_factory=dict)
    skill_context: str = ""
    tool_policy: Dict[str, Any] = field(default_factory=dict)
    source_snapshots: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "packet_version": self.packet_version,
            "task": self.task,
            "wave_kind": self.wave_kind,
            "contract_hash": self.contract_hash,
            "work_item": self.work_item,
            "scope": self.scope,
            "phase": dict(self.phase),
            "team_brief": dict(self.team_brief),
            "agent_role_hint": self.agent_role_hint,
            "verification_layer": self.verification_layer,
            "scope_test_hints": dict(self.scope_test_hints),
            "direct_dependencies": list(self.direct_dependencies),
            "allowed_artifacts": list(self.allowed_artifacts),
            "conflict_keys": list(self.conflict_keys),
            "acceptance_criteria": list(self.acceptance_criteria),
            "recent_evidence": list(self.recent_evidence),
            "diagnostics": list(self.diagnostics),
            "selected_skills": list(self.selected_skills),
            "worker_protocol": list(self.worker_protocol),
            "locked_artifacts": list(self.locked_artifacts),
            "repair_packet": dict(self.repair_packet),
            "skill_context": self.skill_context,
            "tool_policy": dict(self.tool_policy),
            "source_snapshots": self.source_snapshots,
        }

    def render(self, max_chars: int) -> str:
        dependency_lines = []
        for dependency in self.direct_dependencies:
            interfaces = dependency.get("provided_interfaces") or []
            dependency_lines.append(
                f"- {dependency.get('id')}: status={dependency.get('status', 'unknown')}; "
                f"artifacts={', '.join(dependency.get('target_artifacts') or []) or 'None'}; "
                f"interfaces={interfaces or 'None'}"
            )
        parts = [
            "## Agent Input Packet",
            f"- packet_version: {self.packet_version}",
            f"- contract_hash: {self.contract_hash}",
            f"- wave_kind: {self.wave_kind}",
            f"- verification_layer: {self.verification_layer or 'None'}",
            f"- phase_id: {self.phase.get('phase_id', self.work_item.get('inputs', {}).get('phase_id', '')) or 'None'}",
            f"- phase_goal: {self.phase.get('goal', '') or 'None'}",
            f"- work_item_id: {self.work_item.get('id', '')}",
            f"- kind: {self.work_item.get('kind', '')}",
            f"- title: {self.work_item.get('title', '')}",
            f"- scope: {self.scope.get('id', '')} ({self.scope.get('type', '')})",
            f"- team_kind: {self.team_brief.get('team_kind', 'unknown')}",
            f"- team_role_hint: {self.agent_role_hint or 'default'}",
            f"- allowed_artifacts: {', '.join(self.allowed_artifacts) or 'None'}",
            f"- conflict_keys: {', '.join(self.conflict_keys) or 'None'}",
            f"- selected_skills: {', '.join(self.selected_skills) or 'None'}",
            f"- locked_artifacts: {', '.join(self.locked_artifacts) or 'None'}",
            f"- dependency_policy: {self.work_item.get('dependency_policy', 'done')}",
            "### Direct Dependencies",
            "\n".join(dependency_lines) if dependency_lines else "- None",
            "### Provided Interfaces",
            self._render_list(self.work_item.get("provided_interfaces") or []),
            "### Required Interfaces",
            self._render_list(self.work_item.get("required_interfaces") or []),
            "### Acceptance Criteria",
            self._render_list(self.acceptance_criteria),
        ]
        if self.scope_test_hints:
            parts.extend(["### Scope Test Hints", self._render_mapping(self.scope_test_hints)])
        if self.recent_evidence:
            parts.extend(["### Recent Evidence", self._render_list(self.recent_evidence)])
        if self.diagnostics:
            parts.extend(["### Structured Repair Diagnostics", self._render_list(self.diagnostics)])
        if self.repair_packet:
            parts.extend(["### Repair Packet", self._render_mapping(self.repair_packet)])
        if self.worker_protocol:
            parts.extend(["### Worker Protocol", self._render_list(self.worker_protocol)])
        if self.source_snapshots:
            parts.extend(["### Target Artifact Source Snapshots", self.source_snapshots])
        if self.skill_context:
            parts.extend(["### Relevant Skills", self.skill_context])
        parts.extend(
            [
                "### Tool Policy",
                self._render_mapping(self.tool_policy),
            ]
        )
        return ContextManager._fit_text("\n".join(parts), max_chars)

    @staticmethod
    def _render_list(values: List[Any]) -> str:
        if not values:
            return "- None"
        return "\n".join(f"- {value}" for value in values)

    @staticmethod
    def _render_mapping(value: Dict[str, Any]) -> str:
        if not value:
            return "- None"
        return "\n".join(f"- {key}: {AgentInputPacket._render_scalar(val)}" for key, val in sorted(value.items()))

    @staticmethod
    def _render_scalar(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "None"
        if isinstance(value, dict):
            return value and str(value) or "None"
        return str(value)


class ContextManager:
    """Controls large model inputs across history, summaries, and skills."""

    def __init__(self, config: Config, agents: Sequence[str], memory_window: int | None = None):
        self.config = config
        self._llm_local = threading.local()
        self._memory_local = threading.local()
        self.agents = list(agents)
        self.budget = ContextBudget(
            max_messages=memory_window or config.MEMORY_WINDOW,
            max_chars=config.CONTEXT_MAX_CHARS,
            skill_chars=config.CONTEXT_SKILL_CHARS,
        )
        self.history: Dict[str, List[GeneralState]] = {}
        self.memory = self.history
        self.skills: Dict[str, SkillSpec] = {}
        self.skill_router = SkillRouter()
        self.logger = get_logger(config.LOG_PATH)
        self._lock = threading.RLock()
        if config.ENABLE_BUILTIN_SKILLS:
            self.load_builtin_skills()
        self.load_skills_from_paths(config.SKILL_PATHS)

    @property
    def backend(self) -> LLMBackend:
        backend = getattr(self._llm_local, "backend", None)
        if backend is None:
            backend = build_backend(self.config)
            self._llm_local.backend = backend
        return backend

    def add_message(self, agent_name: str, message: GeneralState) -> None:
        with self._lock:
            self.history.setdefault(agent_name, []).append(message)
            need_summary = len(self.history[agent_name]) > self.budget.max_messages

        if need_summary:
            self.summarize_memory(agent_name)

    def get_memory(self, agent_name: str) -> List[GeneralState]:
        with self._lock:
            return list(self.history.get(agent_name, []))

    @staticmethod
    def memory_key(agent_name: str, *, run_id: str = "", scope_id: str = "") -> str:
        pieces = [piece for piece in (run_id, scope_id, agent_name) if piece]
        return "::".join(pieces) if pieces else agent_name

    def set_active_memory_key(self, agent_name: str, key: str) -> None:
        active = dict(getattr(self._memory_local, "active_keys", {}) or {})
        active[agent_name] = key
        self._memory_local.active_keys = active

    def clear_active_memory_key(self, agent_name: str) -> None:
        active = dict(getattr(self._memory_local, "active_keys", {}) or {})
        active.pop(agent_name, None)
        self._memory_local.active_keys = active

    def _active_memory_key(self, agent_name: str) -> str:
        active = getattr(self._memory_local, "active_keys", {}) or {}
        return str(active.get(agent_name) or agent_name)

    def build_message_history(self, agent_name: str) -> List[dict[str, str]]:
        agent_name = self._active_memory_key(agent_name)
        history = []
        for state in self.get_memory(agent_name):
            content = state.output or state.thinking or ""
            if not content:
                continue
            role = "system" if "<summary" in content else "assistant"
            history.append({"role": role, "content": content})
        return self._fit_messages(history, self.budget.max_chars)

    def register_skill(self, skill: SkillSpec) -> None:
        with self._lock:
            self.skills[skill.name] = skill

    def load_builtin_skills(self) -> None:
        from ContractCoding.knowledge import BUILTIN_SKILL_RECORDS

        for record in BUILTIN_SKILL_RECORDS:
            self.register_skill(SkillSpec(**record))

    def load_skills_from_paths(self, raw_paths: str | Iterable[str] | None) -> None:
        for path in self._iter_skill_paths(raw_paths):
            try:
                self.register_skill(self._load_skill(path))
            except OSError as exc:
                self.logger.warning("Unable to load skill %s: %s", path, exc)

    def skills_for(self, work_kind: str) -> List[SkillSpec]:
        with self._lock:
            skills = list(self.skills.values())
        return sorted(
            [skill for skill in skills if skill.accepts(work_kind)],
            key=lambda skill: (skill.priority, skill.name),
        )

    def render_skill_context(self, work_kind: str = "", max_chars: int | None = None) -> str:
        budget = max_chars if max_chars is not None else self.budget.skill_chars
        rendered = [skill.render() for skill in self.skills_for(work_kind)]
        return self._fit_text("\n\n".join(rendered), budget)

    def select_skills_for_item(
        self,
        *,
        item: WorkItem,
        scope: WorkScope,
        contract: ContractSpec,
        diagnostics: List[Dict[str, Any]] | None = None,
    ) -> SkillSelection:
        with self._lock:
            skills = list(self.skills.values())
        return self.skill_router.select(
            skills=skills,
            item=item,
            scope=scope,
            contract=contract,
            diagnostics=list(diagnostics or []),
        )

    def render_work_item_context(self, item: Any, scope: Any = None, max_chars: int | None = None) -> str:
        budget = max_chars if max_chars is not None else min(self.budget.max_chars, 8000)
        parts = [
            "## Focused WorkItem Context",
            f"- id: {getattr(item, 'id', '')}",
            f"- kind: {getattr(item, 'kind', '')}",
            f"- scope: {getattr(item, 'scope_id', '')}",
            f"- target_artifacts: {', '.join(getattr(item, 'target_artifacts', []) or []) or 'None'}",
            f"- conflict_keys: {', '.join(getattr(item, 'conflict_keys', []) or []) or 'None'}",
            f"- dependency_policy: {getattr(item, 'dependency_policy', 'done')}",
        ]
        if scope is not None:
            parts.append(f"- execution_plane_policy: {getattr(scope, 'execution_plane_policy', 'auto')}")
        if getattr(item, "provided_interfaces", None):
            parts.append("### Provided Interfaces")
            parts.extend(f"- {interface}" for interface in item.provided_interfaces)
        if getattr(item, "required_interfaces", None):
            parts.append("### Required Interfaces")
            parts.extend(f"- {interface}" for interface in item.required_interfaces)
        if getattr(item, "acceptance_criteria", None):
            parts.append("### Acceptance Criteria")
            parts.extend(f"- {criterion}" for criterion in item.acceptance_criteria)
        if getattr(item, "evidence", None):
            parts.append("### Recent Evidence")
            parts.extend(f"- {entry}" for entry in item.evidence[-5:])
        return self._fit_text("\n".join(parts), budget)

    def build_agent_input_packet(
        self,
        *,
        task: str,
        contract: ContractSpec,
        item: WorkItem,
        scope: WorkScope,
        wave_kind: str,
        runtime_items: Iterable[WorkItem] = (),
        source_snapshots: str = "",
    ) -> AgentInputPacket:
        runtime_by_id = {runtime_item.id: runtime_item for runtime_item in runtime_items}
        contract_by_id = contract.item_by_id()
        direct_dependencies: List[Dict[str, Any]] = []
        for dependency_id in item.depends_on:
            dependency = contract_by_id.get(dependency_id)
            if dependency is None:
                continue
            runtime_dependency = runtime_by_id.get(dependency_id)
            dependency_record = dependency.to_contract_record()
            dependency_record["status"] = runtime_dependency.status if runtime_dependency else "PENDING"
            dependency_record["evidence"] = list((runtime_dependency.evidence if runtime_dependency else [])[-3:])
            direct_dependencies.append(dependency_record)

        diagnostics = self._diagnostics_for_item(item)
        skill_selection = self.select_skills_for_item(
            item=item,
            scope=scope,
            contract=contract,
            diagnostics=diagnostics,
        )
        return AgentInputPacket(
            packet_version="5",
            task=task,
            wave_kind=wave_kind,
            contract_hash=contract.content_hash(),
            work_item=item.to_contract_record(),
            scope=scope.to_record(),
            phase=self._phase_slice(contract, item),
            team_brief={
                "scope_id": scope.id,
                "team_kind": scope.team_policy.get("team_kind", self._default_team_kind(item, scope)),
                "workspace_plane": scope.team_policy.get("workspace_plane", scope.execution_plane_policy),
                "promotion_policy": dict(scope.promotion_policy),
                "interface_stability": scope.interface_stability,
                "scope_interfaces": list(scope.interfaces),
            },
            agent_role_hint=item.team_role_hint,
            verification_layer=str(
                item.verification_policy.get("layer")
                or item.verification_policy.get("system_gate")
                or ("self_check" if wave_kind == "implementation" else "work_item_verification")
            ),
            scope_test_hints=self._scope_test_hints(contract, item, scope),
            direct_dependencies=direct_dependencies,
            allowed_artifacts=list(item.target_artifacts),
            conflict_keys=list(item.conflict_keys),
            acceptance_criteria=list(item.acceptance_criteria or contract.acceptance_criteria),
            recent_evidence=list(item.evidence[-5:]),
            diagnostics=diagnostics,
            selected_skills=skill_selection.selected_names,
            worker_protocol=skill_selection.worker_protocol,
            locked_artifacts=skill_selection.locked_artifacts,
            repair_packet=skill_selection.repair_packet,
            skill_context=self.skill_router.render(skill_selection, self.budget.skill_chars),
            tool_policy={
                "execution_plane": scope.execution_plane_policy,
                "allowed_artifacts": list(item.target_artifacts),
                "allowed_conflict_keys": list(item.conflict_keys),
                "allowed_tools": list(skill_selection.allowed_tools),
                "locked_artifacts": list(skill_selection.locked_artifacts),
                "risk_level": item.risk_level,
                "worker_protocol_version": "2",
            },
            source_snapshots=source_snapshots,
        )

    @staticmethod
    def _phase_slice(contract: ContractSpec, item: WorkItem) -> Dict[str, Any]:
        phase_id = str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")).strip()
        if not phase_id:
            return {}
        for phase in contract.phase_plan:
            if phase.phase_id == phase_id:
                record = phase.to_record()
                return {
                    "phase_id": record.get("phase_id", ""),
                    "goal": record.get("goal", ""),
                    "mode": record.get("mode", ""),
                    "entry_conditions": record.get("entry_conditions", []),
                    "phase_gate": record.get("phase_gate", {}),
                }
        return {"phase_id": phase_id}

    @staticmethod
    def _scope_test_hints(contract: ContractSpec, item: WorkItem, scope: WorkScope) -> Dict[str, Any]:
        contract_ownership = dict(contract.test_ownership or {})
        scope_ownership = dict(scope.test_ownership or {})
        scope_tests = dict(contract_ownership.get("scope_tests", {}))
        hints = {
            "policy": contract_ownership.get("policy", "scope_or_tests_scope"),
            "owned_tests": scope_ownership.get("owned_tests", scope_tests.get(scope.id, [])),
            "item_test_artifacts": list(item.inputs.get("test_artifacts", [])),
            "interface_required": bool(item.required_interfaces),
        }
        return {key: value for key, value in hints.items() if value not in (None, "", [], {})}

    @staticmethod
    def _diagnostics_for_item(item: WorkItem) -> List[Dict[str, Any]]:
        records = item.inputs.get("diagnostics", [])
        if isinstance(records, dict):
            records = [records]
        diagnostics = [dict(record) for record in records if isinstance(record, dict)]
        latest = item.inputs.get("latest_diagnostic")
        if isinstance(latest, dict) and latest not in diagnostics:
            diagnostics.insert(0, dict(latest))
        return diagnostics[:5]

    @staticmethod
    def _default_team_kind(item: WorkItem, scope: WorkScope) -> str:
        if scope.type == "integration":
            return "integration"
        if scope.type == "tests":
            return "tests"
        if item.kind in {"research", "doc", "data", "ops"}:
            return item.kind
        return "coding"

    def summarize_memory(self, agent_name: str) -> None:
        with self._lock:
            states = self.history.get(agent_name, [])
            if len(states) <= self.budget.max_messages:
                return
            summarize_count = max(1, self.budget.max_messages // 2)
            states_to_summarize = states[:summarize_count]
            remaining_states = states[summarize_count:]

        conversation_text = "\n".join(
            f"{state.role}: {state.output or state.thinking}" for state in states_to_summarize
        )
        prompt = (
            "Summarize this agent history into a concise durable memory. "
            "Keep decisions, artifacts, constraints, blockers, and outcomes. "
            "Return only the summary.\n\n"
            f"{conversation_text}"
        )
        summary_text = self.backend.chat([{"role": "user", "content": prompt}]).content
        self.logger.info("Summary for %s: %s", agent_name, summary_text)

        summary_message = GeneralState(
            task=states_to_summarize[-1].task,
            sub_task=states_to_summarize[-1].sub_task,
            role="system",
            thinking="Summary of previous context.",
            output=f"<summary_of_previous_turns>\n{summary_text}\n</summary_of_previous_turns>",
        )
        with self._lock:
            self.history[agent_name] = [summary_message] + remaining_states

    def merge_memory(self, states: List[GeneralState]) -> GeneralState:
        if not states:
            return GeneralState(
                task="",
                sub_task="",
                role="system",
                thinking="",
                output="",
                next_agents=[],
                task_requirements=None,
            )
        if len(states) == 1:
            return states[0]

        separator = "\n\n" + "=" * 20 + " MERGED INPUT " + "=" * 20 + "\n\n"
        merged_next_agents = []
        merged_task_requirements: dict[str, str] = {}
        for state in states:
            if state.next_agents:
                merged_next_agents.extend(state.next_agents)
            if state.task_requirements:
                for key, value in state.task_requirements.items():
                    merged_task_requirements[key] = merged_task_requirements.get(key, "") + "\n" + value

        return GeneralState(
            task=states[0].task,
            sub_task=separator.join(
                f"# Sub Task from Upstream Agent {index + 1}:\n{state.sub_task}"
                for index, state in enumerate(states)
                if state.sub_task
            ),
            role="system",
            thinking=separator.join(
                f"# Thinking from Upstream Agent {index + 1}:\n{state.thinking}"
                for index, state in enumerate(states)
                if state.thinking
            ),
            output=separator.join(
                f"# Output from Upstream Agent {index + 1}:\n{state.output}"
                for index, state in enumerate(states)
                if state.output
            ),
            next_agents=sorted(set(merged_next_agents)),
            task_requirements=merged_task_requirements,
        )

    def _normalize_agent_name(self, agent_name: str) -> str:
        normalized_map = {re.sub(r"[^a-z0-9]", "", name.lower()): name for name in self.agents}
        normalized_input = re.sub(r"[^a-z0-9]", "", agent_name.lower())
        return normalized_map.get(normalized_input, agent_name)

    def _iter_skill_paths(self, raw_paths: str | Iterable[str] | None) -> Iterable[str]:
        if not raw_paths:
            return []
        if isinstance(raw_paths, str):
            pieces = re.split(r"[,{}]+".format(re.escape(os.pathsep)), raw_paths)
        else:
            pieces = list(raw_paths)

        candidates: List[str] = []
        for piece in pieces:
            path = os.path.abspath(os.path.expanduser(str(piece).strip()))
            if not path:
                continue
            if os.path.isdir(path):
                skill_md = os.path.join(path, "SKILL.md")
                if os.path.exists(skill_md):
                    candidates.append(skill_md)
                else:
                    candidates.extend(
                        os.path.join(path, name)
                        for name in sorted(os.listdir(path))
                        if name.endswith(".md")
                    )
            else:
                candidates.append(path)
        return candidates

    def _load_skill(self, path: str) -> SkillSpec:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        metadata, body = self._split_frontmatter(content)
        prompt_body = body.strip() or content
        name = str(metadata.get("name") or "").strip() or os.path.splitext(os.path.basename(path))[0]
        heading = re.search(r"^#\s+(.+)$", prompt_body, re.MULTILINE)
        if heading:
            name = str(metadata.get("name") or "").strip() or heading.group(1).strip()
        description = str(metadata.get("description") or "").strip()
        for line in prompt_body.splitlines():
            if description:
                break
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped
                break
        kinds = self._metadata_list(metadata, "allowed_work_kinds", "allowed-work-kinds") or self._extract_allowed_work_kinds(content)
        tool_hints = (
            self._metadata_list(metadata, "tool_hints", "tool-hints", "allowed-tools")
            or self._extract_csv_field(content, "tool_hints")
        )
        return SkillSpec(
            name=name,
            description=description,
            prompt=prompt_body,
            path=path,
            allowed_work_kinds=kinds,
            trigger_keywords=(
                self._metadata_list(metadata, "trigger_keywords", "trigger-keywords")
                or self._extract_csv_field(content, "trigger_keywords")
            ),
            evidence_requirements=(
                self._metadata_list(metadata, "evidence_requirements", "evidence-requirements")
                or self._extract_csv_field(content, "evidence_requirements")
            ),
            tool_hints=tool_hints,
            risk_policy=(
                str(metadata.get("risk_policy") or metadata.get("risk-policy") or "").strip()
                or self._extract_scalar_field(content, "risk_policy")
            ),
            priority=self._metadata_int(metadata, "priority", default=self._extract_int_field(content, "priority", default=50)),
        )

    @classmethod
    def _split_frontmatter(cls, content: str) -> tuple[Dict[str, Any], str]:
        if not content.startswith("---"):
            return {}, content
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, content
        end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_index = index
                break
        if end_index is None:
            return {}, content
        raw_metadata = "\n".join(lines[1:end_index])
        body = "\n".join(lines[end_index + 1 :])
        return cls._parse_frontmatter(raw_metadata), body

    @staticmethod
    def _parse_frontmatter(raw_metadata: str) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        current_key = ""
        for raw_line in raw_metadata.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            stripped = raw_line.strip()
            if stripped.startswith("- ") and current_key:
                metadata.setdefault(current_key, [])
                if isinstance(metadata[current_key], list):
                    metadata[current_key].append(stripped[2:].strip().strip("'\""))
                continue
            if ":" not in raw_line:
                continue
            key, raw_value = raw_line.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            current_key = key
            if not value:
                metadata[key] = []
                continue
            if value.startswith("[") and value.endswith("]"):
                metadata[key] = [
                    item.strip().strip("'\"")
                    for item in value[1:-1].split(",")
                    if item.strip()
                ]
            else:
                metadata[key] = value.strip().strip("'\"")
        return metadata

    @classmethod
    def _metadata_list(cls, metadata: Dict[str, Any], *field_names: str) -> List[str]:
        for field_name in field_names:
            if field_name not in metadata:
                continue
            value = metadata[field_name]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str):
                separator = r"[,; ]+" if field_name == "allowed-tools" else r"[,;]+"
                return [item.strip().strip("'\"") for item in re.split(separator, value) if item.strip()]
        return []

    @staticmethod
    def _metadata_int(metadata: Dict[str, Any], field_name: str, default: int) -> int:
        try:
            return int(str(metadata.get(field_name, default)).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_allowed_work_kinds(content: str) -> List[str]:
        match = re.search(r"allowed_work_kinds\s*:\s*([^\n]+)", content, re.IGNORECASE)
        if not match:
            return ["*"]
        kinds = [item.strip().lower() for item in re.split(r"[, ]+", match.group(1)) if item.strip()]
        return kinds or ["*"]

    @staticmethod
    def _extract_csv_field(content: str, field_name: str) -> List[str]:
        match = re.search(rf"{re.escape(field_name)}\s*:\s*([^\n]+)", content, re.IGNORECASE)
        if not match:
            return []
        return [item.strip() for item in re.split(r"[,;]", match.group(1)) if item.strip()]

    @staticmethod
    def _extract_scalar_field(content: str, field_name: str) -> str:
        match = re.search(rf"{re.escape(field_name)}\s*:\s*([^\n]+)", content, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @classmethod
    def _extract_int_field(cls, content: str, field_name: str, default: int) -> int:
        raw = cls._extract_scalar_field(content, field_name)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _fit_text(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        marker = "\n\n[context truncated]\n"
        return text[: max(0, max_chars - len(marker))] + marker

    def _fit_messages(self, messages: List[dict[str, str]], max_chars: int) -> List[dict[str, str]]:
        if max_chars <= 0:
            return messages
        kept: List[dict[str, str]] = []
        total = 0
        for message in reversed(messages):
            content = message.get("content", "")
            next_total = total + len(content)
            if kept and next_total > max_chars:
                break
            if next_total > max_chars:
                message = {**message, "content": self._fit_text(content, max_chars)}
                next_total = len(message["content"])
            kept.append(message)
            total = next_total
        return list(reversed(kept))
