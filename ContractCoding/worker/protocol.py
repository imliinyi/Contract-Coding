"""LLM port — narrow protocol so passes don't depend on a specific backend.

The legacy v1 LLM stack (`ContractCoding.llm.base.LLMBackend`) returns rich
tool-call deltas; the v2 worker only needs a textual prompt → textual
completion contract. Conversion lives in `bridge.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class LLMRequest:
    system_prompt: str
    user_prompt: str
    role_hint: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 1024


@dataclass
class LLMResult:
    text: str
    raw: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"
    cost_estimate_usd: float = 0.0
    latency_ms: float = 0.0


class LLMPort(Protocol):
    """Minimal LLM contract used by passes."""

    def complete(self, request: LLMRequest) -> LLMResult:
        ...


class NullLLMPort:
    """Deterministic offline backend.

    Returns a JSON-shaped echo response that downstream passes can parse, so
    the pipeline can be unit-tested without an API key. The `text` field is
    formed from the user_prompt's first non-empty line + role_hint.
    """

    def __init__(self, fixed_text: Optional[str] = None) -> None:
        self.fixed_text = fixed_text

    def complete(self, request: LLMRequest) -> LLMResult:
        if self.fixed_text is not None:
            return LLMResult(text=self.fixed_text)
        # Synthesize a response per role
        if request.role_hint == "planner":
            text = (
                '{"subtasks": [{"title": "implement", "files": [], "boundaries": []}], '
                '"open_questions": []}'
            )
        elif request.role_hint == "implementer":
            text = '{"artifacts": [], "decisions": [], "uncertainty": 0.5}'
        elif request.role_hint == "judge":
            text = '{"verdict": "approve", "reasons": ["null backend"], "blockers": []}'
        else:
            text = "{}"
        return LLMResult(text=text)
