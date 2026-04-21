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
    target_files: Set[str] = field(default_factory=set)
    primary_target: Optional[str] = None
    target_module: Optional[str] = None
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

    def build_spec(self, agent_name: str, state: GeneralState) -> TaskSpec:
        target_files: Set[str] = set()
        target_module: Optional[str] = None
        if state.sub_task:
            module_match = re.search(r"^Module team:\s*(.+)$", state.sub_task, re.MULTILINE)
            if module_match:
                target_module = module_match.group(1).strip()

            lines = state.sub_task.splitlines()
            collecting_targets = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Target files in this module wave:") or stripped.startswith("Files to review:"):
                    collecting_targets = True
                    continue
                if collecting_targets:
                    if not stripped and target_files:
                        break
                    bullet_match = re.match(r"[-*]\s+`?([^`]+?\.[A-Za-z0-9_]+)`?$", stripped)
                    if bullet_match:
                        target_files.add(bullet_match.group(1).strip())
                        continue
                    if target_files:
                        break

            if not target_files:
                match = re.search(
                    r"\b(?:Implement/Fix|Fix|Continue implementation of)\s+([^\s]+\.[A-Za-z0-9_]+)\b",
                    state.sub_task,
                )
                if match:
                    target_files.add(match.group(1).strip())

        owned_tasks = self.document_manager.get_tasks_by_owner(agent_name)
        if target_module:
            owned_files = {
                str(task["file"])
                for task in owned_tasks
                if str(task.get("module", "")) == target_module and task.get("file")
            }
        else:
            owned_files = {str(task["file"]) for task in owned_tasks if task.get("file")}

        owned_files.update(target_files)
        primary_target = next(iter(sorted(target_files)), None)
        return TaskSpec(
            agent_name=agent_name,
            target_files=target_files,
            primary_target=primary_target,
            target_module=target_module,
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
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_files:
            return []
        missing = []
        for target_file in sorted(spec.target_files):
            abs_target = os.path.join(self.workspace_dir, target_file)
            if not os.path.exists(abs_target):
                missing.append(f"Required target file was not created or updated: {target_file}")
        return missing

    def _validate_placeholders(self, spec: TaskSpec) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_files:
            return []
        problems = []
        for target_file in sorted(spec.target_files):
            abs_target = os.path.join(self.workspace_dir, target_file)
            if not os.path.exists(abs_target):
                continue

            _, extension = os.path.splitext(abs_target)
            if extension.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
                continue

            try:
                with open(abs_target, "r", encoding="utf-8") as handle:
                    content = handle.read()
            except OSError as exc:
                problems.append(f"Unable to inspect generated target file {target_file}: {exc}")
                continue

            for pattern in PLACEHOLDER_PATTERNS:
                if pattern.search(content):
                    problems.append(
                        f"Placeholder implementation remains in {target_file}: pattern '{pattern.pattern}'"
                    )
        return problems

    def _validate_status_update(self, spec: TaskSpec) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_files:
            return []
        errors = []
        for target_file in sorted(spec.target_files):
            task = self.document_manager.preview_task(target_file)
            if not task or task.status not in {"DONE", "VERIFIED"}:
                errors.append(f"Contract status for {target_file} was not advanced to DONE/VERIFIED.")
        return errors

    def _record_validation_errors(self, spec: TaskSpec, errors: Iterable[str]) -> None:
        if not spec.target_files:
            return
        for target_file in sorted(spec.target_files):
            self.document_manager.record_task_failure(
                file_path=target_file,
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
                sorted(spec.target_files),
                validation_errors,
            )
            self._record_validation_errors(spec, validation_errors)

        return TaskResult(
            output_state=output_state,
            changed_files=changed_files,
            validation_errors=validation_errors,
        )
