"""Shared LLM backend interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional


@dataclass
class ToolIntent:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ToolIntent":
        arguments = dict(payload.get("arguments", payload.get("args", {})) or {})
        if not arguments and payload.get("artifact"):
            arguments["path"] = payload.get("artifact")
            if "content" in payload:
                arguments["content"] = payload.get("content")
        if "path" not in arguments and payload.get("path"):
            arguments["path"] = payload.get("path")
        if "content" not in arguments and payload.get("content") is not None:
            arguments["content"] = payload.get("content")
        return cls(
            name=str(payload.get("name", payload.get("tool", ""))).strip(),
            arguments=arguments,
            reason=str(payload.get("reason", "")),
        )


@dataclass
class LLMResponse:
    content: str
    backend: str
    raw: Any = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_intents: List[ToolIntent] = field(default_factory=list)

    def __str__(self) -> str:
        return self.content


class LLMBackend(ABC):
    name: str = "unknown"
    supports_native_tools: bool = False

    @abstractmethod
    def chat(self, messages: List[Dict[str, Any]]) -> LLMResponse:
        raise NotImplementedError

    def chat_with_image(self, messages: List[Dict[str, Any]], images: List[str]) -> LLMResponse:
        if images:
            messages = list(messages) + [
                {
                    "role": "user",
                    "content": f"{len(images)} image input(s) were provided, but backend {self.name} does not support images.",
                }
            ]
        return self.chat(messages)

    def chat_with_tools(self, messages: List[Dict[str, Any]], tools: List[Any]) -> LLMResponse:
        tool_names = ", ".join(_tool_name(tool) for tool in tools) or "none"
        tool_prompt = {
            "role": "system",
            "content": (
                "This backend does not execute tools natively. If a tool is needed, return a "
                "JSON object inside <tool_intents>...</tool_intents> with a `tool_intents` list. "
                f"Available tools: {tool_names}."
            ),
        }
        return self.chat([tool_prompt, *messages])

    def plan_tool_calls(self, messages: List[Dict[str, Any]], tools: List[Any]) -> List[ToolIntent]:
        response = self.chat_with_tools(messages, tools)
        return response.tool_intents or parse_tool_intents(response.content)


def _tool_name(tool: Any) -> str:
    if hasattr(tool, "__name__"):
        return str(tool.__name__)
    if isinstance(tool, dict):
        return str(tool.get("function", {}).get("name", "unknown_tool"))
    return str(tool)


def parse_tool_intents(text: str) -> List[ToolIntent]:
    if not text:
        return []
    match = re.search(r"<tool_intents>\s*(.*?)\s*</tool_intents>", text, re.DOTALL)
    raw = match.group(1) if match else text.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = payload.get("tool_intents", payload.get("intents", payload.get("tools", payload.get("calls", []))))
    if not isinstance(payload, list):
        return []
    return [
        ToolIntent.from_mapping(item)
        for item in payload
        if isinstance(item, dict) and (item.get("name") or item.get("tool"))
    ]
