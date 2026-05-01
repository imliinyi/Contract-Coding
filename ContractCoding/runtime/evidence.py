"""Evidence collection helpers for run ledger and verifier decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class EvidenceRecord:
    kind: str
    summary: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def render(self) -> str:
        return f"[{self.kind}] {self.summary}"


class EvidenceCollector:
    def __init__(self):
        self.records: List[EvidenceRecord] = []

    def add(self, kind: str, summary: str, **payload: Any) -> EvidenceRecord:
        record = EvidenceRecord(kind=kind, summary=summary, payload=payload)
        self.records.append(record)
        return record

    def extend_from_task_result(self, result) -> List[EvidenceRecord]:
        records = []
        changed_files = sorted(getattr(result, "changed_files", []) or [])
        if changed_files:
            records.append(self.add("changed_files", ", ".join(changed_files), files=changed_files))
        errors = list(getattr(result, "validation_errors", []) or [])
        if errors:
            records.append(self.add("validation_errors", "; ".join(errors), errors=errors))
        return records

    def render(self) -> List[str]:
        return [record.render() for record in self.records]
