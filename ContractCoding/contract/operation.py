"""Typed contract exchange primitives.

This is the formal replacement for cross-team chat. Agents may propose
operations, but only the deterministic reducer/auditor may accept them into
the contract state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from typing import Any, Dict, List, Optional


class OperationKind(str, Enum):
    DECLARE_API = "declare_api"
    REQUEST_API = "request_api"
    PROPOSE_API_CHANGE = "propose_api_change"
    DECLARE_DEPENDENCY = "declare_dependency"
    REPORT_BLOCKER = "report_blocker"
    SUBMIT_EVIDENCE = "submit_evidence"
    REQUEST_REVIEW = "request_review"
    PUBLISH_CAPSULE = "publish_capsule"
    RECORD_DECISION = "record_decision"


class OperationStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ObligationKind(str, Enum):
    MISSING_CAPSULE = "missing_capsule"
    API_MISMATCH = "api_mismatch"
    REVIEW_FAILED = "review_failed"
    VALIDATION_MISSING = "validation_missing"
    BLOCKER = "blocker"


class ObligationStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


@dataclass
class ContractOperation:
    op_id: str
    kind: OperationKind
    from_team: str
    target_ref: str
    payload: Dict[str, Any] = field(default_factory=dict)
    target_team: str = ""
    from_role: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    related_task_ids: List[str] = field(default_factory=list)
    rationale: str = ""
    status: OperationStatus = OperationStatus.PROPOSED
    rejection_reasons: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    accepted_at: Optional[float] = None
    supersedes: Optional[str] = None

    @classmethod
    def new(
        cls,
        *,
        kind: OperationKind | str,
        from_team: str,
        target_ref: str,
        payload: Optional[Dict[str, Any]] = None,
        target_team: str = "",
        from_role: str = "",
        evidence_refs: Optional[List[str]] = None,
        related_task_ids: Optional[List[str]] = None,
        rationale: str = "",
    ) -> "ContractOperation":
        return cls(
            op_id=f"op:{uuid.uuid4().hex[:12]}",
            kind=OperationKind(kind),
            from_team=from_team,
            target_team=target_team,
            from_role=from_role,
            target_ref=target_ref,
            payload=dict(payload or {}),
            evidence_refs=list(evidence_refs or []),
            related_task_ids=list(related_task_ids or []),
            rationale=rationale,
        )

    def accept(self) -> None:
        self.status = OperationStatus.ACCEPTED
        self.rejection_reasons = []
        self.accepted_at = time.time()

    def reject(self, reasons: List[str]) -> None:
        self.status = OperationStatus.REJECTED
        self.rejection_reasons = list(reasons)

    def to_record(self) -> Dict[str, Any]:
        return {
            "op_id": self.op_id,
            "kind": _enum_value(self.kind),
            "from_team": self.from_team,
            "from_role": self.from_role,
            "target_team": self.target_team,
            "target_ref": self.target_ref,
            "payload": dict(self.payload),
            "evidence_refs": list(self.evidence_refs),
            "related_task_ids": list(self.related_task_ids),
            "rationale": self.rationale,
            "status": _enum_value(self.status),
            "rejection_reasons": list(self.rejection_reasons),
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ContractOperation":
        payload = dict(payload or {})
        kind = str(payload.get("kind", OperationKind.REPORT_BLOCKER.value))
        status = str(payload.get("status", OperationStatus.PROPOSED.value))
        return cls(
            op_id=str(payload.get("op_id", "") or f"op:{uuid.uuid4().hex[:12]}"),
            kind=OperationKind(kind),
            from_team=str(payload.get("from_team", "")),
            from_role=str(payload.get("from_role", "")),
            target_team=str(payload.get("target_team", "")),
            target_ref=str(payload.get("target_ref", "")),
            payload=dict(payload.get("payload", {}) or {}),
            evidence_refs=[str(v) for v in payload.get("evidence_refs", []) or []],
            related_task_ids=[str(v) for v in payload.get("related_task_ids", []) or []],
            rationale=str(payload.get("rationale", "")),
            status=OperationStatus(status),
            rejection_reasons=[str(v) for v in payload.get("rejection_reasons", []) or []],
            created_at=float(payload.get("created_at", time.time())),
            accepted_at=(
                float(payload["accepted_at"]) if payload.get("accepted_at") is not None else None
            ),
            supersedes=payload.get("supersedes"),
        )


@dataclass
class ContractObligation:
    obligation_id: str
    kind: ObligationKind
    team_id: str
    reason: str
    target_ref: str = ""
    target_team: str = ""
    task_ids: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    source_op_id: str = ""
    status: ObligationStatus = ObligationStatus.OPEN
    created_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None

    @classmethod
    def new(
        cls,
        *,
        kind: ObligationKind | str,
        team_id: str,
        reason: str,
        target_ref: str = "",
        target_team: str = "",
        task_ids: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        source_op_id: str = "",
    ) -> "ContractObligation":
        stable = "|".join([str(kind), team_id, target_ref, ",".join(task_ids or [])])
        suffix = uuid.uuid5(uuid.NAMESPACE_URL, stable).hex[:12]
        return cls(
            obligation_id=f"obl:{suffix}",
            kind=ObligationKind(kind),
            team_id=team_id,
            target_team=target_team,
            target_ref=target_ref,
            reason=reason,
            task_ids=list(task_ids or []),
            evidence_refs=list(evidence_refs or []),
            source_op_id=source_op_id,
        )

    def key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            _enum_value(self.kind),
            self.team_id,
            self.target_ref,
            tuple(sorted(self.task_ids)),
        )

    def resolve(self, evidence_refs: Optional[List[str]] = None) -> None:
        self.status = ObligationStatus.RESOLVED
        self.evidence_refs = list(evidence_refs or self.evidence_refs)
        self.resolved_at = time.time()

    def to_record(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "kind": _enum_value(self.kind),
            "team_id": self.team_id,
            "target_team": self.target_team,
            "target_ref": self.target_ref,
            "reason": self.reason,
            "task_ids": list(self.task_ids),
            "evidence_refs": list(self.evidence_refs),
            "source_op_id": self.source_op_id,
            "status": _enum_value(self.status),
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ContractObligation":
        payload = dict(payload or {})
        kind = str(payload.get("kind", ObligationKind.BLOCKER.value))
        status = str(payload.get("status", ObligationStatus.OPEN.value))
        return cls(
            obligation_id=str(payload.get("obligation_id", "") or f"obl:{uuid.uuid4().hex[:12]}"),
            kind=ObligationKind(kind),
            team_id=str(payload.get("team_id", "")),
            target_team=str(payload.get("target_team", "")),
            target_ref=str(payload.get("target_ref", "")),
            reason=str(payload.get("reason", "")),
            task_ids=[str(v) for v in payload.get("task_ids", []) or []],
            evidence_refs=[str(v) for v in payload.get("evidence_refs", []) or []],
            source_op_id=str(payload.get("source_op_id", "")),
            status=ObligationStatus(status),
            created_at=float(payload.get("created_at", time.time())),
            resolved_at=(
                float(payload["resolved_at"]) if payload.get("resolved_at") is not None else None
            ),
        )
