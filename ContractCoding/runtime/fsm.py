"""Small WorkItem finite-state machine.

WorkItem state is intentionally compact. Step attempts may end in ``ERROR``,
but items themselves move to ``BLOCKED`` when runtime recovery is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Set


WORK_ITEM_STATUSES = {
    "PENDING",
    "READY",
    "RUNNING",
    "DONE",
    "VERIFIED",
    "BLOCKED",
}

STATUS_ALIASES = {
    "TODO": "PENDING",
    "PLANNED": "PENDING",
    "IN_PROGRESS": "RUNNING",
    "PRODUCED": "DONE",
    "SYSTEM_CHECKED": "DONE",
    "ERROR": "BLOCKED",
    "FAILED": "BLOCKED",
    "STALE": "BLOCKED",
    "CANCELLED": "BLOCKED",
}

TRANSITIONS: Dict[str, Set[str]] = {
    "PENDING": {"READY", "RUNNING", "BLOCKED"},
    "READY": {"RUNNING", "DONE", "BLOCKED"},
    "RUNNING": {"DONE", "VERIFIED", "BLOCKED"},
    "DONE": {"VERIFIED", "BLOCKED"},
    "VERIFIED": set(),
    "BLOCKED": {"READY", "RUNNING"},
}


def normalize_work_item_status(status: str | None) -> str:
    value = str(status or "PENDING").strip().upper()
    value = STATUS_ALIASES.get(value, value)
    return value if value in WORK_ITEM_STATUSES else "PENDING"


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    reason: str = ""


class WorkItemStateMachine:
    def can_transition(self, current: str, target: str) -> TransitionDecision:
        current_status = normalize_work_item_status(current)
        target_status = normalize_work_item_status(target)
        if current_status == target_status:
            return TransitionDecision(True, "No-op transition.")
        allowed = TRANSITIONS.get(current_status, set())
        if target_status in allowed:
            return TransitionDecision(True, "Allowed transition.")
        return TransitionDecision(
            False,
            f"Invalid WorkItem transition: {current_status} -> {target_status}.",
        )
