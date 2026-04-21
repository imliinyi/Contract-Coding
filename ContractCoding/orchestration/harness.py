"""Execution harness and validators for agent tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Iterable, List, Optional, Set

from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


IMPLEMENTATION_ROLES = {"Backend_Engineer", "Frontend_Engineer", "Algorithm_Engineer"}
PLACEHOLDER_PATTERNS = (
    re.compile(r"^\s*pass\s*(#.*)?$", re.MULTILINE),
    re.compile(r"TODO", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
)


@dataclass
class TaskSpec:
    agent_name: str
    target_file: Optional[str]
    module_name: Optional[str] = None
    target_files: Set[str] = field(default_factory=set)
    owned_files: Set[str] = field(default_factory=set)


@dataclass
class TaskResult:
    output_state: GeneralState
    changed_files: Set[str]
    validation_errors: List[str]


class TaskHarness:
    """Wrap agent execution with workspace and contract validators."""

    def __init__(self, config: Config, document_manager: DocumentManager):
        self.config = config
        self.document_manager = document_manager
        self.workspace_dir = os.path.abspath(config.WORKSPACE_DIR)
        self.logger = get_logger(config.LOG_PATH)

    @staticmethod
    def _extract_target_files(sub_task: str) -> List[str]:
        if not sub_task:
            return []

        targets: List[str] = []
        seen = set()

        primary_match = re.search(r"Primary target file:\s*`?([^\s`]+)`?", sub_task, re.IGNORECASE)
        if primary_match:
            target = primary_match.group(1).strip()
            seen.add(target)
            targets.append(target)

        line_patterns = [
            re.compile(r"^\s*-\s*`([^`]+)`", re.MULTILINE),
            re.compile(r"\b(?:Implement/Fix|Fix|Continue implementation of)\s+([^\s]+\.[A-Za-z0-9_]+)\b"),
        ]
        for pattern in line_patterns:
            for match in pattern.finditer(sub_task):
                target = match.group(1).strip()
                if target in seen:
                    continue
                seen.add(target)
                targets.append(target)
        return targets

    def build_spec(self, agent_name: str, state: GeneralState) -> TaskSpec:
        module_name = None
        if state.sub_task:
            module_match = re.search(r"Module Cell:\s*(.+)", state.sub_task)
            if module_match:
                module_name = module_match.group(1).strip()

        extracted_target_files = self._extract_target_files(state.sub_task or "")
        target_file = extracted_target_files[0] if extracted_target_files else None
        target_files = set(extracted_target_files)

        owned_files = {task["file"] for task in self.document_manager.get_tasks_by_owner(agent_name)}
        if module_name:
            module_tasks = self.document_manager.get_tasks_by_module(module_name)
            module_owned_files = {
                task["file"]
                for task in module_tasks
                if task.get("owner") == agent_name
            }
            if module_owned_files:
                owned_files = module_owned_files

        if target_files:
            owned_files.update(target_files)

        return TaskSpec(
            agent_name=agent_name,
            target_file=target_file,
            module_name=module_name,
            target_files=target_files,
            owned_files=owned_files,
        )

    def _snapshot(self) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        if not os.path.isdir(self.workspace_dir):
            return snapshot
        for root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [name for name in dirs if name not in {".git", "__pycache__"}]
            for file_name in files:
                abs_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(abs_path, self.workspace_dir)
                try:
                    snapshot[rel_path] = os.stat(abs_path).st_mtime_ns
                except OSError:
                    continue
        return snapshot

    def _diff_snapshots(self, before: dict[str, int], after: dict[str, int]) -> Set[str]:
        changed = set()
        for rel_path, mtime in after.items():
            if before.get(rel_path) != mtime:
                changed.add(rel_path)
        return changed

    def _validate_changed_scope(self, spec: TaskSpec, changed_files: Set[str]) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.owned_files:
            return []
        allowed_prefixes = {".contractcoding/", "history/"}
        violations = []
        for rel_path in changed_files:
            if rel_path in spec.owned_files:
                continue
            if any(rel_path.startswith(prefix) for prefix in allowed_prefixes):
                continue
            violations.append(f"Unexpected file modification outside owned scope: {rel_path}")
        return violations

    def _validate_target_file(self, spec: TaskSpec) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_file:
            return []
        abs_target = os.path.join(self.workspace_dir, spec.target_file)
        if not os.path.exists(abs_target):
            return [f"Required target file was not created or updated: {spec.target_file}"]
        return []

    def _validate_placeholders(self, spec: TaskSpec) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_file:
            return []

        abs_target = os.path.join(self.workspace_dir, spec.target_file)
        if not os.path.exists(abs_target):
            return []

        _, extension = os.path.splitext(abs_target)
        if extension.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            return []

        try:
            with open(abs_target, "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError as exc:
            return [f"Unable to inspect generated target file {spec.target_file}: {exc}"]

        problems = []
        for pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(content):
                problems.append(f"Placeholder implementation remains in {spec.target_file}: pattern '{pattern.pattern}'")
        return problems

    def _validate_status_update(self, spec: TaskSpec) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_file:
            return []
        task = self.document_manager.preview_task(spec.target_file)
        if not task or task.status not in {"DONE", "VERIFIED"}:
            return [f"Contract status for {spec.target_file} was not advanced to DONE/VERIFIED."]
        return []

    def _record_validation_errors(self, spec: TaskSpec, errors: Iterable[str]) -> None:
        if not spec.target_file:
            return
        self.document_manager.record_task_failure(
            file_path=spec.target_file,
            issues=list(errors),
            agent_name=spec.agent_name,
        )

    def execute(self, agent, agent_name: str, state: GeneralState, next_available_agents: list, memory_processor) -> TaskResult:
        spec = self.build_spec(agent_name, state)
        before_snapshot = self._snapshot()
        output_state = agent._execute_agent(
            state=state,
            next_available_agents=next_available_agents,
            document_manager=self.document_manager,
            memory_processor=memory_processor,
        )
        after_snapshot = self._snapshot()
        changed_files = self._diff_snapshots(before_snapshot, after_snapshot)

        validation_errors = []
        validation_errors.extend(self._validate_target_file(spec))
        validation_errors.extend(self._validate_changed_scope(spec, changed_files))
        validation_errors.extend(self._validate_placeholders(spec))
        validation_errors.extend(self._validate_status_update(spec))

        if validation_errors:
            self.logger.warning(
                "Harness validation failed for %s on %s: %s",
                agent_name,
                spec.target_file,
                validation_errors,
            )
            self._record_validation_errors(spec, validation_errors)

        return TaskResult(
            output_state=output_state,
            changed_files=changed_files,
            validation_errors=validation_errors,
        )
