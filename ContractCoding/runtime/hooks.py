"""Minimal hook manager for tool execution observability."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List


class HookManager:
    def __init__(self):
        self._handlers: Dict[str, List[Callable[..., None]]] = defaultdict(list)

    def register(self, event_name: str, handler: Callable[..., None]) -> None:
        self._handlers[event_name].append(handler)

    def emit(self, event_name: str, **payload: Any) -> None:
        for handler in list(self._handlers.get(event_name, [])):
            handler(**payload)
