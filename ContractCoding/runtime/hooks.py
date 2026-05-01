"""Minimal in-process hooks for ContractCoding Runtime V4 events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ContractCoding.runtime.store import RunStore


HookHandler = Callable[["HookContext"], Optional["HookResult"]]


@dataclass
class HookContext:
    event: str
    run_id: str = ""
    task_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    ok: bool = True
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class HookManager:
    def __init__(self, store: Optional[RunStore] = None, enabled: bool = True):
        self.store = store
        self.enabled = enabled
        self._handlers: Dict[str, List[HookHandler]] = {}

    def register(self, event: str, handler: HookHandler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(
        self,
        event: str,
        *,
        run_id: str = "",
        task_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> List[HookResult]:
        context = HookContext(event=event, run_id=run_id, task_id=task_id, payload=dict(payload or {}))
        results: List[HookResult] = []
        if not self.enabled:
            return results

        for handler in list(self._handlers.get(event, [])):
            try:
                result = handler(context) or HookResult()
            except Exception as exc:
                result = HookResult(ok=False, error=str(exc))
            results.append(result)
            if not result.ok:
                self._record_error(context, result)

        if self.store is not None and run_id:
            self.store.append_event(run_id, f"hook:{event}", {"task_id": task_id, "payload": context.payload})
        return results

    def _record_error(self, context: HookContext, result: HookResult) -> None:
        if self.store is None or not context.run_id:
            return
        self.store.append_event(
            context.run_id,
            "hook_error",
            {
                "event": context.event,
                "task_id": context.task_id,
                "error": result.error,
                "payload": result.payload,
            },
        )
