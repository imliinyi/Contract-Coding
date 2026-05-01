"""Execution harness and validators for agent tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Iterable, List, Optional, Set

from ContractCoding.config import Config
from ContractCoding.execution.planes import ExecutionPlaneManager, ExecutionPlanePromotionError
from ContractCoding.execution.workspace import get_current_workspace, workspace_scope
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


IMPLEMENTATION_ROLES = {
    "Backend_Engineer",
    "Frontend_Engineer",
    "Algorithm_Engineer",
    "Test_Engineer",
    "Recovery_Orchestrator",
}
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
    execution_plane: Optional[str] = None
    owned_files: Set[str] = field(default_factory=set)


@dataclass
class TaskResult:
    output_state: GeneralState
    changed_files: Set[str]
    validation_errors: List[str]


class TaskHarness:
    """Wrap agent execution with workspace and contract validators."""

    def __init__(self, config: Config):
        self.config = config
        self.workspace_dir = os.path.abspath(config.WORKSPACE_DIR)
        self.execution_plane_manager = ExecutionPlaneManager(config)
        self.logger = get_logger(config.LOG_PATH)

    def build_spec(self, agent_name: str, state: GeneralState) -> TaskSpec:
        target_files: Set[str] = set()
        wave_allowed_files: Set[str] = set()
        target_module: Optional[str] = None
        execution_plane: Optional[str] = None
        if state.sub_task:
            module_match = re.search(r"^Module team:\s*(.+)$", state.sub_task, re.MULTILINE)
            if module_match:
                target_module = module_match.group(1).strip()
            plane_match = re.search(r"^Execution plane:\s*(.+)$", state.sub_task, re.MULTILINE)
            if plane_match:
                execution_plane = plane_match.group(1).strip().lower()

            lines = state.sub_task.splitlines()
            collecting_targets = False
            collecting_wave_allowed = False
            for line in lines:
                stripped = line.strip()
                if (
                    stripped.startswith("Target files in this module wave:")
                    or stripped.startswith("Target files:")
                    or stripped.startswith("Target artifacts:")
                    or stripped.startswith("Files to review:")
                ):
                    collecting_targets = True
                    collecting_wave_allowed = False
                    continue
                if stripped.startswith("Wave allowed artifacts:"):
                    collecting_wave_allowed = True
                    collecting_targets = False
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
                if collecting_wave_allowed:
                    if not stripped:
                        collecting_wave_allowed = False
                        continue
                    bullet_match = re.match(r"[-*]\s+`?([^`]+?\.[A-Za-z0-9_]+)`?$", stripped)
                    if bullet_match:
                        wave_allowed_files.add(bullet_match.group(1).strip())
                        continue
                    collecting_wave_allowed = False

            if not target_files:
                match = re.search(
                    r"\b(?:Implement/Fix|Fix|Continue implementation of)\s+([^\s]+\.[A-Za-z0-9_]+)\b",
                    state.sub_task,
                )
                if match:
                    target_files.add(match.group(1).strip())

        owned_files = set(target_files) | set(wave_allowed_files)
        primary_target = next(iter(sorted(target_files)), None)
        return TaskSpec(
            agent_name=agent_name,
            target_files=target_files,
            primary_target=primary_target,
            target_module=target_module,
            execution_plane=execution_plane,
            owned_files=owned_files,
        )

    def _snapshot(self, workspace_dir: str) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        if not os.path.isdir(workspace_dir):
            return snapshot
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [name for name in dirs if name not in {".git", "__pycache__"}]
            for file_name in files:
                abs_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(abs_path, workspace_dir)
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

    def _validate_target_file(self, spec: TaskSpec, workspace_dir: str) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_files:
            return []
        missing = []
        for target_file in sorted(spec.target_files):
            abs_target = os.path.join(workspace_dir, target_file)
            if not os.path.exists(abs_target):
                missing.append(f"Required target file was not created or updated: {target_file}")
        return missing

    def _validate_placeholders(self, spec: TaskSpec, workspace_dir: str) -> List[str]:
        if spec.agent_name not in IMPLEMENTATION_ROLES or not spec.target_files:
            return []
        problems = []
        for target_file in sorted(spec.target_files):
            abs_target = os.path.join(workspace_dir, target_file)
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

    def _record_validation_errors(self, spec: TaskSpec, errors: Iterable[str]) -> None:
        return

    def execute(self, agent, agent_name: str, state: GeneralState, next_available_agents: list, context_manager) -> TaskResult:
        spec = self.build_spec(agent_name, state)
        active_workspace = get_current_workspace(self.workspace_dir)
        if os.path.abspath(active_workspace) != self.workspace_dir:
            return self._execute_in_current_workspace(
                agent=agent,
                agent_name=agent_name,
                state=state,
                next_available_agents=next_available_agents,
                context_manager=context_manager,
                spec=spec,
                workspace_dir=active_workspace,
            )

        plane = self.execution_plane_manager.acquire(
            module_name=spec.target_module or spec.primary_target or "root",
            isolated=agent_name in IMPLEMENTATION_ROLES,
            mode_override=spec.execution_plane,
        )
        try:
            before_snapshot = self._snapshot(plane.working_dir)
            with workspace_scope(plane.working_dir):
                output_state = agent._execute_agent(
                    state=state,
                    next_available_agents=next_available_agents,
                    context_manager=context_manager,
                )
            after_snapshot = self._snapshot(plane.working_dir)
            changed_files = self._diff_snapshots(before_snapshot, after_snapshot)

            validation_errors = []
            validation_errors.extend(self._validate_target_file(spec, plane.working_dir))
            validation_errors.extend(self._validate_changed_scope(spec, changed_files))
            validation_errors.extend(self._validate_placeholders(spec, plane.working_dir))

            if validation_errors:
                self.logger.warning(
                    "Harness validation failed for %s on %s: %s",
                    agent_name,
                    sorted(spec.target_files),
                    validation_errors,
                )
                self._record_validation_errors(spec, validation_errors)
            else:
                try:
                    promoted = self.execution_plane_manager.promote(plane, changed_files)
                    if plane.isolated:
                        self.logger.info(
                            "Promoted execution plane %s for module %s into workspace: %s",
                            plane.mode,
                            plane.module_name,
                            sorted(promoted),
                        )
                except ExecutionPlanePromotionError as exc:
                    validation_errors.append(str(exc))
                    self.logger.warning(
                        "Harness promotion rejected for %s on %s: %s",
                        agent_name,
                        sorted(spec.target_files),
                        exc,
                    )
                    self._record_validation_errors(spec, validation_errors)

            return TaskResult(
                output_state=output_state,
                changed_files=changed_files,
                validation_errors=validation_errors,
            )
        finally:
            self.execution_plane_manager.cleanup(plane)

    def _execute_in_current_workspace(
        self,
        agent,
        agent_name: str,
        state: GeneralState,
        next_available_agents: list,
        context_manager,
        spec: TaskSpec,
        workspace_dir: str,
    ) -> TaskResult:
        before_snapshot = self._snapshot(workspace_dir)
        with workspace_scope(workspace_dir):
            output_state = agent._execute_agent(
                state=state,
                next_available_agents=next_available_agents,
                context_manager=context_manager,
            )
        after_snapshot = self._snapshot(workspace_dir)
        changed_files = self._diff_snapshots(before_snapshot, after_snapshot)

        validation_errors = []
        validation_errors.extend(self._validate_target_file(spec, workspace_dir))
        validation_errors.extend(self._validate_changed_scope(spec, changed_files))
        validation_errors.extend(self._validate_placeholders(spec, workspace_dir))
        if validation_errors:
            self.logger.warning(
                "Harness validation failed for %s on %s in active workspace %s: %s",
                agent_name,
                sorted(spec.target_files),
                workspace_dir,
                validation_errors,
            )
            self._record_validation_errors(spec, validation_errors)

        return TaskResult(
            output_state=output_state,
            changed_files=changed_files,
            validation_errors=validation_errors,
        )
