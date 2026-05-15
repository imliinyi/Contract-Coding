"""Cross-team event log.

Append-only audit stream of structured events. Cross-team coordination is
handled by typed ContractOperation/ContractObligation records; events preserve
observable runtime history and should not be used as a natural-language message
bus.

Events live in `/events.log` (jsonl) under the registry root. The file is
append-only: Coordinator and DeathSpiralDetector tail it; consumers may build
local projections by topic/team.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from .margin import MarginAnnotation


class EventKind(str, Enum):
    PLAN_FROZEN = "plan_frozen"
    INTENT_REFINED = "intent_refined"
    TEAM_ACTIVATED = "team_activated"
    CAPSULE_PROPOSED = "capsule_proposed"
    CAPSULE_DRAFTED = "capsule_drafted"
    CAPSULE_LOCKED = "capsule_locked"
    CAPSULE_EVOLVED = "capsule_evolved"
    CAPSULE_BROKEN = "capsule_broken"
    SLICE_STARTED = "slice_started"
    SLICE_INSPECTED = "slice_inspected"
    SLICE_IMPLEMENTED = "slice_implemented"
    SLICE_REVIEWED = "slice_reviewed"
    SLICE_VERIFIED = "slice_verified"
    SLICE_REJECTED = "slice_rejected"
    FAILURE_LOGGED = "failure_logged"
    DEATH_SPIRAL_DETECTED = "death_spiral_detected"
    ESCALATED = "escalated"
    ESCALATION_RESOLVED = "escalation_resolved"
    PROGRESS = "progress"
    DECISION = "decision"
    UNCERTAINTY = "uncertainty"
    INTERACTION = "interaction"


@dataclass
class Event:
    kind: EventKind
    team_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    margin: MarginAnnotation = field(default_factory=MarginAnnotation.system)
    event_id: str = field(default_factory=lambda: f"evt:{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            try:
                self.kind = EventKind(self.kind)
            except ValueError:
                raise ValueError(f"Unknown EventKind: {self.kind!r}") from None

    def to_record(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "team_id": self.team_id,
            "payload": dict(self.payload),
            "margin": self.margin.to_record(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "Event":
        payload = dict(payload or {})
        return cls(
            event_id=str(payload.get("event_id", "") or f"evt:{uuid.uuid4().hex[:12]}"),
            kind=EventKind(str(payload.get("kind", "progress"))),
            team_id=str(payload.get("team_id", "")),
            payload=dict(payload.get("payload", {}) or {}),
            margin=MarginAnnotation.from_mapping(payload.get("margin", {}) or {}),
            created_at=float(payload.get("created_at", time.time())),
        )


class EventLog:
    """Thread-safe append-only JSONL event log."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: Event) -> Event:
        line = json.dumps(event.to_record(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
        return event

    def read(
        self,
        *,
        since_ts: float = 0.0,
        kinds: Optional[Iterable[EventKind]] = None,
        team_id: str = "",
        limit: int = 0,
    ) -> List[Event]:
        if not os.path.exists(self.path):
            return []
        kinds_set = {EventKind(k) for k in kinds} if kinds else None
        out: List[Event] = []
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event = Event.from_mapping(raw)
                    if event.created_at < since_ts:
                        continue
                    if team_id and event.team_id != team_id:
                        continue
                    if kinds_set and event.kind not in kinds_set:
                        continue
                    out.append(event)
                    if limit and len(out) >= limit:
                        break
        return out

    def latest(self, *, team_id: str = "", n: int = 20) -> List[Event]:
        events = self.read(team_id=team_id)
        return events[-n:]

    def clear(self) -> None:
        with self._lock:
            if os.path.exists(self.path):
                os.remove(self.path)
