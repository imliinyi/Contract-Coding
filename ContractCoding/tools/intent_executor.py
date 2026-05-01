"""Execute model-proposed tool intents through ContractCoding policy."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, Iterable, List

from ContractCoding.llm.base import ToolIntent
from ContractCoding.runtime.hooks import HookManager
from ContractCoding.tools.governor import ToolGovernor


@dataclass
class ToolExecutionResult:
    name: str
    arguments: Dict[str, Any]
    allowed: bool
    output: str
    reason: str = ""
    validation_status: str = ""
    touched_files: List[str] | None = None
    validation_errors: List[str] | None = None
    rolled_back: bool = False

    def to_record(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "arguments": self.arguments,
            "allowed": self.allowed,
            "output": self.output,
            "reason": self.reason,
        }
        if self.validation_status:
            payload["validation_status"] = self.validation_status
        if self.touched_files:
            payload["touched_files"] = list(self.touched_files)
        if self.validation_errors:
            payload["validation_errors"] = list(self.validation_errors)
        if self.rolled_back:
            payload["rolled_back"] = True
        return payload


def execute_tool_intents(
    intents: Iterable[ToolIntent],
    tools: List[Any],
    governor: ToolGovernor,
    hook_manager: HookManager | None = None,
    run_id: str = "",
    task_id: str = "",
    tool_execution_observer: Any | None = None,
) -> List[ToolExecutionResult]:
    available = _build_tool_map(tools)
    results: List[ToolExecutionResult] = []

    for intent in intents:
        if hook_manager is not None:
            hook_manager.emit(
                "pre_tool_use",
                run_id=run_id,
                task_id=task_id,
                payload={"name": intent.name, "arguments": dict(intent.arguments)},
            )
        decision = governor.decide(intent.name, intent.arguments)
        if not decision.allowed:
            result = ToolExecutionResult(
                name=intent.name,
                arguments=dict(intent.arguments),
                allowed=False,
                output="",
                reason=decision.reason,
            )
            results.append(result)
            if hook_manager is not None:
                hook_manager.emit("post_tool_use", run_id=run_id, task_id=task_id, payload=result.to_record())
            continue

        tool = available.get(intent.name)
        if tool is None:
            result = ToolExecutionResult(
                name=intent.name,
                arguments=dict(intent.arguments),
                allowed=False,
                output="",
                reason=f"Tool {intent.name} is not registered.",
            )
            results.append(result)
            if hook_manager is not None:
                hook_manager.emit("post_tool_use", run_id=run_id, task_id=task_id, payload=result.to_record())
            continue

        try:
            if tool_execution_observer is not None and hasattr(tool_execution_observer, "before_tool"):
                tool_execution_observer.before_tool(intent)
            output = str(tool(**intent.arguments))
            result = ToolExecutionResult(
                name=intent.name,
                arguments=dict(intent.arguments),
                allowed=True,
                output=output,
                reason=decision.reason,
            )
            if tool_execution_observer is not None and hasattr(tool_execution_observer, "after_tool"):
                result = tool_execution_observer.after_tool(intent, result)
        except Exception as exc:
            result = ToolExecutionResult(
                name=intent.name,
                arguments=dict(intent.arguments),
                allowed=True,
                output=f"Tool {intent.name} execution error: {exc}",
                reason=decision.reason,
            )
        results.append(result)
        if hook_manager is not None:
            hook_manager.emit("post_tool_use", run_id=run_id, task_id=task_id, payload=result.to_record())
    return results


def render_tool_results(results: Iterable[ToolExecutionResult]) -> str:
    records = [result.to_record() for result in results]
    return json.dumps({"tool_results": records}, ensure_ascii=False, indent=2)


def _build_tool_map(tools: List[Any]) -> Dict[str, Any]:
    available: Dict[str, Any] = {}
    for tool in tools:
        if hasattr(tool, "__name__"):
            available[tool.__name__] = tool
            continue
        if isinstance(tool, dict) and "function" in tool:
            name = tool["function"].get("name")
            implementation = tool.get("implementation")
            if name and implementation:
                available[str(name)] = implementation
    return available
