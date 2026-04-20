"""Collaborative document manager backed by structured contract state."""

from __future__ import annotations

from copy import deepcopy
import json
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ContractCoding.memory.audit import check_missing_specs
from ContractCoding.memory.contract_state import (
    ContractState,
    TaskBlock,
    canonicalize_section_key,
)
from ContractCoding.utils.log import get_logger


class DocumentManager:
    """Single-writer contract state with Markdown rendering for humans."""

    def __init__(self):
        self.logger = get_logger()
        self._lock = threading.RLock()
        self._state = ContractState.empty()
        self._version = 0
        self._history: Dict[int, ContractState] = {0: self._state.copy()}
        self._aggregate_mode = False
        self._queued_actions: List[Dict[str, Any]] = []
        self._last_conflicts: List[str] = []
        self._persist()

    def _persist(self) -> None:
        with open("document.md", "w", encoding="utf-8") as handle:
            handle.write(self._state.to_markdown())

    def get(self) -> str:
        return self._state.to_markdown()

    def get_version(self) -> int:
        return self._version

    def get_last_conflicts(self) -> List[str]:
        return list(self._last_conflicts)

    def get_tasks(self) -> List[Dict[str, object]]:
        with self._lock:
            return self._state.list_tasks()

    def get_task(self, file_path: str) -> Optional[TaskBlock]:
        with self._lock:
            task = self._state.get_task(file_path)
            return task.copy() if task else None

    def preview_task(self, file_path: str) -> Optional[TaskBlock]:
        with self._lock:
            state = self._state
            if self._aggregate_mode and self._queued_actions:
                state, _ = self._apply_actions(self._state, self._queued_actions, detect_conflicts=False)
            task = state.get_task(file_path)
            return task.copy() if task else None

    def get_tasks_by_owner(self, owner: str) -> List[Dict[str, object]]:
        with self._lock:
            return [task.to_record() for task in self._state.get_tasks_by_owner(owner)]

    def validate_contract_structure(self) -> List[str]:
        with self._lock:
            rendered = self._state.to_markdown()
            tasks = self._state.list_tasks()

        validation_errors: List[str] = []
        missing_specs = check_missing_specs(rendered)
        if missing_specs:
            validation_errors.extend(
                [
                    f"File defined in Project Structure but missing from Sub-Tasks (Symbolic API Specifications): {path}"
                    for path in missing_specs
                ]
            )

        for task in tasks:
            file_path = str(task.get("file", "Unknown"))
            owner = str(task.get("owner", "Unknown"))
            status = str(task.get("status", "TODO"))
            if owner == "Unknown":
                validation_errors.append(f"Task for {file_path} missing 'Owner' field.")
            if status not in {"TODO", "IN_PROGRESS", "ERROR", "DONE", "VERIFIED"}:
                validation_errors.append(f"Task for {file_path} has invalid Status: {status}")

        return validation_errors

    def execute_actions(self, actions: list) -> None:
        if not isinstance(actions, list):
            return
        with self._lock:
            next_state, conflicts = self._apply_actions(self._state, actions, detect_conflicts=False)
            self._commit_state(next_state, conflicts)

    def begin_layer_aggregation(self, base_version: int) -> None:
        with self._lock:
            self._aggregate_mode = True
            self._queued_actions = []
            self._last_conflicts = []

    def is_aggregating(self) -> bool:
        return self._aggregate_mode

    def queue_actions(self, actions: list) -> None:
        if not isinstance(actions, list):
            return
        with self._lock:
            if not self._aggregate_mode:
                self.execute_actions(actions)
                return
            self._queued_actions.extend(actions)

    def commit_layer_aggregation(self) -> None:
        with self._lock:
            if not self._aggregate_mode:
                return
            next_state, conflicts = self._apply_actions(self._state, self._queued_actions, detect_conflicts=True)
            self._aggregate_mode = False
            self._queued_actions = []
            self._commit_state(next_state, conflicts)

    def record_task_failure(self, file_path: str, issues: Iterable[str], agent_name: str) -> None:
        action = {
            "type": "task_validation_failure",
            "agent_name": agent_name,
            "file": file_path,
            "issues": list(issues),
        }
        with self._lock:
            if self._aggregate_mode:
                self._queued_actions.append(action)
            else:
                next_state, conflicts = self._apply_actions(self._state, [action], detect_conflicts=False)
                self._commit_state(next_state, conflicts)

    def _commit_state(self, state: ContractState, conflicts: List[str]) -> None:
        self._state = state
        self._version += 1
        self._history[self._version] = state.copy()
        self._last_conflicts = list(conflicts)
        for conflict in conflicts:
            self.logger.warning("Document conflict rejected: %s", conflict)
        self._persist()

    def _apply_actions(
        self,
        base_state: ContractState,
        actions: Iterable[Dict[str, Any]],
        detect_conflicts: bool,
    ) -> Tuple[ContractState, List[str]]:
        work_state = base_state.copy()
        touched_targets: Dict[str, str] = {}
        conflicts: List[str] = []

        for action in actions:
            for op in self._normalize_action(action):
                previous_agent = touched_targets.get(op["target"])
                if detect_conflicts and previous_agent and previous_agent != op["agent_name"]:
                    conflicts.append(
                        f"{op['target']} touched by both {previous_agent} and {op['agent_name']} in the same layer"
                    )
                    continue
                touched_targets[op["target"]] = op["agent_name"]
                self._apply_operation(work_state, op)

        return work_state, conflicts

    def _normalize_action(self, action: Dict[str, Any]) -> List[Dict[str, Any]]:
        action_type = action.get("type")
        agent_name = action.get("agent_name", "unknown_agent")

        if action_type == "task_validation_failure":
            file_path = str(action.get("file", "")).strip()
            if not file_path:
                return []
            return [
                {
                    "kind": "task_failure",
                    "target": f"task:{file_path}",
                    "agent_name": agent_name,
                    "file": file_path,
                    "issues": list(action.get("issues", [])),
                }
            ]

        if action_type not in {"add", "update"}:
            return []

        section = action.get("section")
        content = action.get("content", "")

        if section is None and isinstance(content, str) and content.strip():
            return [
                {
                    "kind": "replace_full_document",
                    "target": "__full_document__",
                    "agent_name": agent_name,
                    "content": content,
                }
            ]

        if isinstance(content, dict):
            return self._normalize_section_patch_ops(content, action_type, agent_name)

        if section is not None:
            section_key = canonicalize_section_key(str(section))
            if not section_key:
                return []
            return self._normalize_section_content_op(section_key, str(content), action_type, agent_name)

        return []

    def _normalize_section_patch_ops(
        self,
        section_patch: Dict[str, Any],
        action_type: str,
        agent_name: str,
    ) -> List[Dict[str, Any]]:
        ops: List[Dict[str, Any]] = []
        for raw_key, raw_value in section_patch.items():
            section_key = canonicalize_section_key(str(raw_key))
            if not section_key:
                continue
            ops.extend(self._normalize_section_content_op(section_key, str(raw_value or ""), action_type, agent_name))
        return ops

    def _normalize_section_content_op(
        self,
        section_key: str,
        body: str,
        action_type: str,
        agent_name: str,
    ) -> List[Dict[str, Any]]:
        body = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if section_key != "Symbolic API Specifications":
            return [
                {
                    "kind": "append_section" if action_type == "add" else "replace_section",
                    "target": f"section:{section_key}",
                    "agent_name": agent_name,
                    "section": section_key,
                    "body": body,
                }
            ]

        parsed_patch = ContractState.empty()
        parsed_patch.replace_section_body(section_key, body)

        ops: List[Dict[str, Any]] = []
        if parsed_patch.symbolic_preamble.strip():
            ops.append(
                {
                    "kind": "append_symbolic_preamble" if action_type == "add" else "replace_symbolic_preamble",
                    "target": "section:Symbolic API Specifications:preamble",
                    "agent_name": agent_name,
                    "body": parsed_patch.symbolic_preamble,
                }
            )

        for file_path in parsed_patch.task_order:
            task = parsed_patch.tasks[file_path].copy()
            ops.append(
                {
                    "kind": "upsert_task",
                    "target": f"task:{file_path}",
                    "agent_name": agent_name,
                    "task": task,
                }
            )
        return ops

    def _apply_operation(self, state: ContractState, op: Dict[str, Any]) -> None:
        kind = op["kind"]
        if kind == "replace_full_document":
            state.replace_full_document(op["content"])
            return
        if kind == "replace_section":
            state.replace_section_body(op["section"], op["body"])
            return
        if kind == "append_section":
            state.append_to_section(op["section"], op["body"])
            return
        if kind == "replace_symbolic_preamble":
            state.symbolic_preamble = op["body"].strip("\n")
            return
        if kind == "append_symbolic_preamble":
            state.append_to_section("Symbolic API Specifications", op["body"])
            return
        if kind == "upsert_task":
            state.upsert_task(op["task"], keep_order=True)
            return
        if kind == "task_failure":
            state.record_task_failure(op["file"], op["issues"], owner=op["agent_name"])
