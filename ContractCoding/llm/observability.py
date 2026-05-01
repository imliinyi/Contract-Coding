"""Backend-neutral LLM observability helpers."""

from __future__ import annotations

from typing import Any, Dict

from ContractCoding.llm.base import LLMResponse


def response_observability(response: LLMResponse, backend: Any = None) -> Dict[str, Any]:
    """Summarize an LLM response without exposing provider secrets or raw payloads."""

    raw = response.raw if isinstance(response.raw, dict) else {}
    events = list(raw.get("adapter_events", []) or [])
    tool_results = list(raw.get("tool_results", []) or [])
    attempts = list(raw.get("attempts", []) or [])
    prompt_tokens = int(response.prompt_tokens or 0)
    completion_tokens = int(response.completion_tokens or 0)
    tool_intents = list(response.tool_intents or [])

    timeout_count = sum(1 for attempt in attempts if str(attempt.get("returncode")) == "timeout")
    empty_response_count = sum(
        1
        for attempt in attempts
        if not str(attempt.get("content_preview", "")).strip()
        and str(attempt.get("returncode", "0")) in {"0", ""}
    )
    tool_intent_count = len(tool_intents)
    for event in events:
        tool_intent_count += len(event.get("tool_intents", []) or [])

    summary = {
        "backend": response.backend or getattr(backend, "name", "unknown"),
        "supports_native_tools": bool(getattr(backend, "supports_native_tools", False)),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tool_intent_count": tool_intent_count,
        "tool_result_count": len(tool_results),
        "event_count": len(events),
        "attempt_count": len(attempts) or int(raw.get("attempt", 0) or 0),
        "timeout_count": timeout_count,
        "empty_response_count": empty_response_count,
        "infra_failure": bool(raw.get("infra_failure", False)),
        "failure_kind": str(raw.get("failure_kind", "") or ""),
        "returncode": raw.get("returncode"),
        "last_event": events[-1] if events else {},
        "stop_reason": str(raw.get("stop_reason", "") or ""),
        "terminal_tool": str((raw.get("terminal_result", {}) or {}).get("tool_name", "") or ""),
        "tool_iterations": int(raw.get("tool_iterations", 0) or 0),
    }
    return {key: value for key, value in summary.items() if value not in ("", None, [], {})}


def payload_observability(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Read normalized observability from a step payload."""

    requirements = dict(payload.get("task_requirements", {}) or {})
    observed = dict(requirements.get("llm_observability", {}) or payload.get("llm_observability", {}) or {})
    return observed
