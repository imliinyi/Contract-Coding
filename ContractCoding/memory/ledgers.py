"""Mutable runtime ledgers — Magentic-One inspired.

Lives strictly under `memory/` because this is *private* team state:
  - TaskLedger — in-flight TaskItems (pending/active/blocked/done).
  - ProgressLedger — append-only progress entries (jsonl on disk).
  - FailureLedger — first-class failed hypotheses (P5 preserve failures).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from typing import Any, Dict, List, Optional

from ..core.margin import MarginAnnotation


class TaskStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    ESCALATED = "escalated"


@dataclass
class TaskItem:
    task_id: str
    title: str
    goal: str
    output_format: str
    tool_whitelist: List[str] = field(default_factory=list)
    boundaries: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    capsule_dependencies: List[str] = field(default_factory=list)
    attempts: int = 0

    def to_record(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "goal": self.goal,
            "output_format": self.output_format,
            "tool_whitelist": list(self.tool_whitelist),
            "boundaries": list(self.boundaries),
            "status": self.status.value,
            "capsule_dependencies": list(self.capsule_dependencies),
            "attempts": int(self.attempts),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TaskItem":
        payload = dict(payload or {})
        return cls(
            task_id=str(payload.get("task_id", "")),
            title=str(payload.get("title", "")),
            goal=str(payload.get("goal", "")),
            output_format=str(payload.get("output_format", "")),
            tool_whitelist=[str(v) for v in payload.get("tool_whitelist", []) or []],
            boundaries=[str(v) for v in payload.get("boundaries", []) or []],
            status=TaskStatus(str(payload.get("status", "pending"))),
            capsule_dependencies=[str(v) for v in payload.get("capsule_dependencies", []) or []],
            attempts=int(payload.get("attempts", 0) or 0),
        )


@dataclass
class TaskLedger:
    team_id: str
    items: List[TaskItem] = field(default_factory=list)

    def add(self, item: TaskItem) -> None:
        self.items.append(item)

    def by_id(self, task_id: str) -> Optional[TaskItem]:
        return next((i for i in self.items if i.task_id == task_id), None)

    def in_status(self, status: TaskStatus) -> List[TaskItem]:
        return [i for i in self.items if i.status == status]

    def to_record(self) -> Dict[str, Any]:
        return {"team_id": self.team_id, "items": [i.to_record() for i in self.items]}

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TaskLedger":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            items=[TaskItem.from_mapping(v) for v in payload.get("items", []) or []],
        )


@dataclass
class ProgressEntry:
    entry_id: str
    task_id: str
    kind: str
    summary: str
    margin: MarginAnnotation = field(default_factory=MarginAnnotation.system)
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_record(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "summary": self.summary,
            "margin": self.margin.to_record(),
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ProgressEntry":
        payload = dict(payload or {})
        return cls(
            entry_id=str(payload.get("entry_id", "") or f"prog:{uuid.uuid4().hex[:8]}"),
            task_id=str(payload.get("task_id", "")),
            kind=str(payload.get("kind", "step")),
            summary=str(payload.get("summary", "")),
            margin=MarginAnnotation.from_mapping(payload.get("margin", {}) or {}),
            payload=dict(payload.get("payload", {}) or {}),
            created_at=float(payload.get("created_at", time.time())),
        )


@dataclass
class FailedHypothesis:
    fingerprint: str
    what_was_tried: str
    why_failed: str
    forbidden_patterns: List[str] = field(default_factory=list)
    related_task_ids: List[str] = field(default_factory=list)
    margin: MarginAnnotation = field(default_factory=MarginAnnotation.system)
    created_at: float = field(default_factory=time.time)

    def to_record(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "what_was_tried": self.what_was_tried,
            "why_failed": self.why_failed,
            "forbidden_patterns": list(self.forbidden_patterns),
            "related_task_ids": list(self.related_task_ids),
            "margin": self.margin.to_record(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "FailedHypothesis":
        payload = dict(payload or {})
        return cls(
            fingerprint=str(payload.get("fingerprint", "")),
            what_was_tried=str(payload.get("what_was_tried", "")),
            why_failed=str(payload.get("why_failed", "")),
            forbidden_patterns=[str(v) for v in payload.get("forbidden_patterns", []) or []],
            related_task_ids=[str(v) for v in payload.get("related_task_ids", []) or []],
            margin=MarginAnnotation.from_mapping(payload.get("margin", {}) or {}),
            created_at=float(payload.get("created_at", time.time())),
        )
