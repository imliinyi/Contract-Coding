"""Git-like change records and conflict facts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Dict, List, Optional


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def sha256_text(content: Optional[str]) -> str:
    if content is None:
        return ""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class FileChange:
    path: str
    before_sha256: str = ""
    after_sha256: str = ""
    status: str = "modified"
    conflict: bool = False
    conflict_reason: str = ""
    expected_sha256: str = ""
    observed_sha256: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "status": self.status,
            "conflict": bool(self.conflict),
            "conflict_reason": self.conflict_reason,
            "expected_sha256": self.expected_sha256,
            "observed_sha256": self.observed_sha256,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "FileChange":
        payload = dict(payload or {})
        return cls(
            path=str(payload.get("path", "")),
            before_sha256=str(payload.get("before_sha256", "")),
            after_sha256=str(payload.get("after_sha256", "")),
            status=str(payload.get("status", "modified")),
            conflict=bool(payload.get("conflict", False)),
            conflict_reason=str(payload.get("conflict_reason", "")),
            expected_sha256=str(payload.get("expected_sha256", "")),
            observed_sha256=str(payload.get("observed_sha256", "")),
        )


@dataclass
class ChangeSet:
    change_set_id: str
    team_id: str
    work_id: str
    base_label: str = ""
    changes: List[FileChange] = field(default_factory=list)

    def has_conflicts(self) -> bool:
        return any(change.conflict for change in self.changes)

    def written_paths(self) -> List[str]:
        return [change.path for change in self.changes if not change.conflict]

    def to_record(self) -> Dict[str, Any]:
        return {
            "change_set_id": self.change_set_id,
            "team_id": self.team_id,
            "work_id": self.work_id,
            "base_label": self.base_label,
            "changes": [change.to_record() for change in self.changes],
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ChangeSet":
        payload = dict(payload or {})
        return cls(
            change_set_id=str(payload.get("change_set_id", "")),
            team_id=str(payload.get("team_id", "")),
            work_id=str(payload.get("work_id", "")),
            base_label=str(payload.get("base_label", "")),
            changes=[FileChange.from_mapping(v) for v in payload.get("changes", []) or []],
        )
