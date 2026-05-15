"""Contract work graph primitives.

These types are public, typed scheduling facts. They intentionally avoid
natural-language message routing: teams publish work items, dependencies, and
conflict boundaries; the scheduler derives parallel waves from those facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class WorkPhase(str, Enum):
    DISCOVER = "discover"
    PLAN = "plan"
    IMPLEMENT = "implement"
    REVIEW = "review"
    VALIDATE = "validate"
    REPAIR = "repair"
    CAPSULE = "capsule"


class WorkStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class ConflictKey:
    """A coarse-grained lock used by the scheduler."""

    scope: str
    name: str

    def render(self) -> str:
        return f"{self.scope}:{self.name}"

    def to_record(self) -> Dict[str, Any]:
        return {"scope": self.scope, "name": self.name}

    @classmethod
    def from_mapping(cls, payload: Any) -> "ConflictKey":
        if isinstance(payload, str):
            if ":" in payload:
                scope, name = payload.split(":", 1)
                return cls(scope=scope, name=name)
            return cls(scope="generic", name=payload)
        payload = dict(payload or {})
        return cls(
            scope=str(payload.get("scope", "generic")),
            name=str(payload.get("name", "")),
        )


@dataclass
class TeamWorkItem:
    """A schedulable contract work item.

    `TaskItem` remains the legacy CLI/input shape. Runtime scheduling uses this
    richer shape, while exposing the same core attributes consumed by the worker
    pipeline (`task_id`, `title`, `goal`, `output_format`, etc.).
    """

    work_id: str
    team_id: str
    title: str
    goal: str
    output_format: str = ""
    phase: WorkPhase = WorkPhase.IMPLEMENT
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    capsule_dependencies: List[str] = field(default_factory=list)
    dependency_ids: List[str] = field(default_factory=list)
    conflict_keys: List[ConflictKey] = field(default_factory=list)
    validation_commands: List[str] = field(default_factory=list)
    tool_whitelist: List[str] = field(default_factory=list)
    boundaries: List[str] = field(default_factory=list)
    status: WorkStatus = WorkStatus.PENDING
    uncertainty: float = 0.0
    parallel_safe: bool = True
    attempts: int = 0
    source_task_id: str = ""

    @property
    def task_id(self) -> str:
        return self.source_task_id or self.work_id

    @task_id.setter
    def task_id(self, value: str) -> None:
        self.source_task_id = value

    def to_record(self) -> Dict[str, Any]:
        status = self.status.value if isinstance(self.status, Enum) else str(self.status)
        phase = self.phase.value if isinstance(self.phase, Enum) else str(self.phase)
        return {
            "work_id": self.work_id,
            "team_id": self.team_id,
            "source_task_id": self.source_task_id,
            "title": self.title,
            "goal": self.goal,
            "output_format": self.output_format,
            "phase": phase,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "capsule_dependencies": list(self.capsule_dependencies),
            "dependency_ids": list(self.dependency_ids),
            "conflict_keys": [k.to_record() for k in self.conflict_keys],
            "validation_commands": list(self.validation_commands),
            "tool_whitelist": list(self.tool_whitelist),
            "boundaries": list(self.boundaries),
            "status": status,
            "uncertainty": float(self.uncertainty),
            "parallel_safe": bool(self.parallel_safe),
            "attempts": int(self.attempts),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamWorkItem":
        payload = dict(payload or {})
        raw_status = str(payload.get("status", "pending"))
        raw_phase = str(payload.get("phase", "implement"))
        work_id = str(payload.get("work_id") or payload.get("task_id") or "")
        source_task_id = str(payload.get("source_task_id") or payload.get("task_id") or work_id)
        return cls(
            work_id=work_id,
            team_id=str(payload.get("team_id", "")),
            source_task_id=source_task_id,
            title=str(payload.get("title", "")),
            goal=str(payload.get("goal", "")),
            output_format=str(payload.get("output_format", "")),
            phase=WorkPhase(raw_phase) if raw_phase in WorkPhase._value2member_map_ else WorkPhase.IMPLEMENT,
            reads=[str(v) for v in payload.get("reads", []) or []],
            writes=[str(v) for v in payload.get("writes", []) or []],
            capsule_dependencies=[str(v) for v in payload.get("capsule_dependencies", []) or []],
            dependency_ids=[str(v) for v in payload.get("dependency_ids", []) or []],
            conflict_keys=[
                ConflictKey.from_mapping(v) for v in payload.get("conflict_keys", []) or []
            ],
            validation_commands=[str(v) for v in payload.get("validation_commands", []) or []],
            tool_whitelist=[str(v) for v in payload.get("tool_whitelist", []) or []],
            boundaries=[str(v) for v in payload.get("boundaries", []) or []],
            status=WorkStatus(raw_status) if raw_status in WorkStatus._value2member_map_ else WorkStatus.PENDING,
            uncertainty=float(payload.get("uncertainty", 0.0) or 0.0),
            parallel_safe=bool(payload.get("parallel_safe", True)),
            attempts=int(payload.get("attempts", 0) or 0),
        )

    @classmethod
    def from_task_item(cls, team_id: str, task: Any) -> "TeamWorkItem":
        """Project the legacy TaskItem shape into the contract work graph."""

        task_id = str(getattr(task, "task_id", ""))
        raw_status = getattr(task, "status", WorkStatus.PENDING)
        status_value = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
        return cls(
            work_id=f"{team_id}:{task_id}" if task_id and ":" not in task_id else task_id,
            team_id=team_id,
            source_task_id=task_id,
            title=str(getattr(task, "title", "")),
            goal=str(getattr(task, "goal", "")),
            output_format=str(getattr(task, "output_format", "")),
            tool_whitelist=[str(v) for v in getattr(task, "tool_whitelist", []) or []],
            boundaries=[str(v) for v in getattr(task, "boundaries", []) or []],
            capsule_dependencies=[
                str(v) for v in getattr(task, "capsule_dependencies", []) or []
            ],
            status=WorkStatus(status_value) if status_value in WorkStatus._value2member_map_ else WorkStatus.PENDING,
            attempts=int(getattr(task, "attempts", 0) or 0),
        )

    def to_task_item(self) -> Any:
        """Return a legacy TaskItem without importing memory at module load."""

        from ..memory.ledgers import TaskItem, TaskStatus

        status_value = self.status.value if isinstance(self.status, Enum) else str(self.status)
        try:
            status = TaskStatus(status_value)
        except ValueError:
            status = TaskStatus.PENDING
        return TaskItem(
            task_id=self.task_id,
            title=self.title,
            goal=self.goal,
            output_format=self.output_format,
            tool_whitelist=list(self.tool_whitelist),
            boundaries=list(self.boundaries),
            status=status,
            capsule_dependencies=list(self.capsule_dependencies),
            attempts=int(self.attempts),
        )


@dataclass
class WorkClaim:
    team_id: str
    work_id: str
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    conflict_keys: List[str] = field(default_factory=list)

    @classmethod
    def from_item(cls, item: TeamWorkItem) -> "WorkClaim":
        return cls(
            team_id=item.team_id,
            work_id=item.work_id,
            reads=list(item.reads),
            writes=list(item.writes),
            conflict_keys=[k.render() for k in item.conflict_keys],
        )


@dataclass
class TeamWave:
    wave_id: str
    items: List[TeamWorkItem] = field(default_factory=list)
    blocked_items: List[TeamWorkItem] = field(default_factory=list)
    reason: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "wave_id": self.wave_id,
            "items": [item.to_record() for item in self.items],
            "blocked_items": [item.to_record() for item in self.blocked_items],
            "reason": self.reason,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamWave":
        payload = dict(payload or {})
        return cls(
            wave_id=str(payload.get("wave_id", "")),
            items=[TeamWorkItem.from_mapping(v) for v in payload.get("items", []) or []],
            blocked_items=[
                TeamWorkItem.from_mapping(v) for v in payload.get("blocked_items", []) or []
            ],
            reason=str(payload.get("reason", "")),
        )


@dataclass
class TeamScheduleReport:
    schedule_id: str
    waves: List[TeamWave] = field(default_factory=list)
    blocked: List[TeamWorkItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "waves": [wave.to_record() for wave in self.waves],
            "blocked": [item.to_record() for item in self.blocked],
            "notes": list(self.notes),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TeamScheduleReport":
        payload = dict(payload or {})
        return cls(
            schedule_id=str(payload.get("schedule_id", "")),
            waves=[TeamWave.from_mapping(v) for v in payload.get("waves", []) or []],
            blocked=[TeamWorkItem.from_mapping(v) for v in payload.get("blocked", []) or []],
            notes=[str(v) for v in payload.get("notes", []) or []],
        )
