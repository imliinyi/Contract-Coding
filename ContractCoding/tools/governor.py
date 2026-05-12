"""Permission gate for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Dict, Iterable, List


TOOL_RISK = {
    "read_file": "read",
    "read_lines": "read",
    "file_tree": "read",
    "list_directory": "read",
    "search_text": "read",
    "inspect_symbol": "read",
    "contract_snapshot": "read",
    "inspect_module_api": "read",
    "report_blocker": "read",
    "submit_result": "read",
    "search_web": "network",
    "create_file": "write",
    "write_file": "write",
    "replace_file": "write",
    "update_file_lines": "write",
    "replace_symbol": "write",
    "add_code": "write",
    "run_code": "execute",
    "run_public_flow": "read",
}


@dataclass
class ToolDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False


@dataclass
class ToolGovernor:
    """Small policy object used by long-running runs before tool execution."""

    approval_mode: str = "suggest"
    allowed_tools: List[str] = field(default_factory=list)
    denied_tools: List[str] = field(default_factory=list)
    allowed_artifacts: List[str] = field(default_factory=list)
    allowed_conflict_keys: List[str] = field(default_factory=list)

    def decide(self, tool_name: str, arguments: Dict[str, Any] | None = None) -> ToolDecision:
        arguments = arguments or {}
        if tool_name in self.denied_tools:
            return ToolDecision(False, f"Tool {tool_name} is explicitly denied.")
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return ToolDecision(False, f"Tool {tool_name} is outside this profile's allowed tool set.")

        risk = TOOL_RISK.get(tool_name, "execute")
        scope_decision = self._decide_artifact_scope(tool_name, arguments, risk)
        if not scope_decision.allowed:
            return scope_decision
        mode = (self.approval_mode or "suggest").strip().lower()
        if mode == "full-auto":
            return ToolDecision(True, "Allowed by full-auto policy.")
        if mode == "auto-edit":
            if risk in {"read", "write", "network"}:
                return ToolDecision(True, "Allowed by auto-edit policy.")
            return ToolDecision(False, f"Tool {tool_name} requires approval in auto-edit mode.", True)
        if risk == "read":
            return ToolDecision(True, "Read-only tool allowed in suggest mode.")
        return ToolDecision(False, f"Tool {tool_name} requires approval in suggest mode.", True)

    def _decide_artifact_scope(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        risk: str,
    ) -> ToolDecision:
        if risk != "write" or not (self.allowed_artifacts or self.allowed_conflict_keys):
            return ToolDecision(True, "No artifact scope restriction applies.")

        path = str(
            arguments.get("path")
            or arguments.get("file_path")
            or arguments.get("target_path")
            or ""
        ).strip()
        if not path:
            return ToolDecision(False, f"Tool {tool_name} is missing a scoped path argument.")

        normalized = self._normalize_path(path)
        allowed_artifacts = {
            self._normalize_path(value)
            for value in self.allowed_artifacts
        }
        allowed_conflicts = set(self.allowed_conflict_keys)
        if normalized in allowed_artifacts or f"artifact:{normalized}" in allowed_conflicts:
            return ToolDecision(True, "Write is within the current work-item artifact scope.")
        return ToolDecision(
            False,
            f"Write to {normalized} is outside the current work-item artifact scope.",
            True,
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = os.path.normpath(str(path or "")).replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    def filter_intents(self, intents: Iterable[Any]) -> List[Any]:
        allowed = []
        for intent in intents:
            name = getattr(intent, "name", "")
            args = getattr(intent, "arguments", {})
            if self.decide(name, args).allowed:
                allowed.append(intent)
        return allowed
