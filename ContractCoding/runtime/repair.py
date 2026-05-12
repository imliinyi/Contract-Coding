"""Repair transaction primitives and a small PatchGuard for OpenAI tools."""

from __future__ import annotations

import os
import py_compile
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class _Snapshot:
    existed: bool
    content: bytes = b""


class PatchGuard:
    """Validate scoped Python writes made by tool calls.

    The guard is intentionally small in the rewrite: it does not own recovery
    policy. It only rejects obvious syntax-breaking writes inside the current
    repair/worker artifact scope.
    """

    WRITE_TOOLS = {"create_file", "write_file", "replace_file", "update_file_lines", "replace_symbol", "add_code"}

    def __init__(
        self,
        workspace_dir: str,
        allowed_artifacts: List[str] | None = None,
        diagnostic_text: str = "",
        timeout: int = 45,
        validate_imports: bool = False,
    ):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.allowed_artifacts = {self._normalize(path) for path in allowed_artifacts or []}
        self.diagnostic_text = diagnostic_text
        self.timeout = timeout
        self.validate_imports = validate_imports
        self._snapshots: Dict[str, _Snapshot] = {}

    def before_tool(self, intent: Any) -> None:
        name = getattr(intent, "name", "")
        if name not in self.WRITE_TOOLS:
            return None
        path = self._path_from_args(getattr(intent, "arguments", {}) or {})
        if not path or not path.endswith(".py"):
            return None
        normalized = self._normalize(path)
        if self.allowed_artifacts and normalized not in self.allowed_artifacts:
            return None
        full_path = os.path.join(self.workspace_dir, normalized)
        if os.path.exists(full_path):
            try:
                with open(full_path, "rb") as handle:
                    self._snapshots[normalized] = _Snapshot(True, handle.read())
            except OSError:
                self._snapshots[normalized] = _Snapshot(False)
        else:
            self._snapshots[normalized] = _Snapshot(False)
        return None

    def after_tool(self, intent: Any, result: Any) -> Any:
        name = getattr(intent, "name", "")
        if name not in self.WRITE_TOOLS or not getattr(result, "allowed", False):
            return result
        path = self._path_from_args(getattr(intent, "arguments", {}) or {})
        if not path or not path.endswith(".py"):
            return result
        normalized = self._normalize(path)
        if self.allowed_artifacts and normalized not in self.allowed_artifacts:
            result.validation_status = "rejected"
            result.validation_errors = [f"{normalized} is outside allowed repair artifacts"]
            result.rolled_back = True
            return result
        full_path = os.path.join(self.workspace_dir, normalized)
        if os.path.exists(full_path):
            try:
                py_compile.compile(full_path, doraise=True)
                result.validation_status = "applied"
                result.touched_files = [normalized]
            except py_compile.PyCompileError as exc:
                self._restore(normalized)
                result.validation_status = "rejected"
                result.validation_errors = [str(exc)]
                result.rolled_back = True
        return result

    def _restore(self, normalized: str) -> None:
        snapshot = self._snapshots.get(normalized)
        full_path = os.path.join(self.workspace_dir, normalized)
        if snapshot is None:
            return
        if snapshot.existed:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as handle:
                handle.write(snapshot.content)
            return
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass

    @staticmethod
    def _path_from_args(args: Dict[str, Any]) -> str:
        return str(args.get("path") or args.get("file_path") or args.get("target_path") or "")

    @staticmethod
    def _normalize(path: str) -> str:
        normalized = os.path.normpath(str(path or "")).replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized
