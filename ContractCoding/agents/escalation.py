"""Escalation + failure-governance primitives."""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ..core.events import Event, EventKind  # noqa: F401
from ..core.margin import AgentRole
from ..memory.ledgers import FailedHypothesis  # noqa: F401
from ..registry import RegistryTool


@dataclass
class Escalation:
    escalation_id: str
    title: str
    team_id: str
    status: str = "open"
    details: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    resolution: Optional[str] = None

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "Escalation":
        return cls(
            escalation_id=str(record.get("escalation_id", "")),
            title=str(record.get("title", "")),
            team_id=str(record.get("team_id", "")),
            status=str(record.get("status", "open")),
            details=dict(record.get("details", {}) or {}),
            created_at=float(record.get("created_at", time.time())),
            resolution=record.get("resolution"),
        )


class EscalationQueue:
    """Append-only escalation board surfaced to the Coordinator & user."""

    def __init__(self, tool: RegistryTool):
        self.tool = tool

    def file(
        self,
        *,
        title: str,
        team_id: str,
        details: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[str]] = None,
    ) -> Escalation:
        escalation_id = (
            "esc:" + hashlib.sha1(f"{team_id}|{title}|{time.time()}".encode()).hexdigest()[:10]
        )
        record = self.tool.file_escalation(
            escalation_id=escalation_id,
            title=title,
            team_id=team_id,
            details=details or {},
            evidence=evidence or [],
        )
        return Escalation.from_record(record)

    def resolve(
        self,
        escalation_id: str,
        *,
        resolution: str,
        evidence: Optional[List[str]] = None,
    ) -> Optional[Escalation]:
        record = self.tool.resolve_escalation(
            escalation_id, resolution=resolution, evidence=evidence
        )
        return Escalation.from_record(record) if record else None

    def list_open(self) -> List[Escalation]:
        return [Escalation.from_record(r) for r in self.tool.list_open_escalations()]


@dataclass
class SpiralVerdict:
    team_id: str
    triggered: bool
    reasons: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


class DeathSpiralDetector:
    """Statistical watchdog. Reads recent events + failures; no LLM."""

    def __init__(
        self,
        tool: RegistryTool,
        *,
        same_fingerprint_threshold: int = 3,
        window_seconds: float = 30 * 60,
        reopen_threshold: int = 2,
    ):
        if tool.actor.role != AgentRole.COORDINATOR:
            raise ValueError(
                "DeathSpiralDetector requires a coordinator-bound RegistryTool "
                "(it must read every team's ledger)"
            )
        self.tool = tool
        self.same_fingerprint_threshold = same_fingerprint_threshold
        self.window_seconds = window_seconds
        self.reopen_threshold = reopen_threshold
        self.queue = EscalationQueue(tool)

    def check_repeated_failures(self, team_id: str) -> Optional[SpiralVerdict]:
        now = time.time()
        since = now - self.window_seconds
        failures = [
            f for f in self.tool.list_failures(team_id)
            if f.created_at >= since
        ]
        if not failures:
            return None
        counts = Counter(f.fingerprint for f in failures)
        reasons: List[str] = []
        evidence: List[str] = []
        for fp, count in counts.items():
            if count >= self.same_fingerprint_threshold:
                reasons.append(
                    f"failure fingerprint {fp!r} repeated {count}× within "
                    f"{int(self.window_seconds/60)}min"
                )
                for f in failures:
                    if f.fingerprint == fp:
                        evidence.append(f"{f.fingerprint}: {f.what_was_tried[:80]} → {f.why_failed[:80]}")
                        if len(evidence) >= 2:
                            break
        return SpiralVerdict(
            team_id=team_id,
            triggered=bool(reasons),
            reasons=reasons,
            evidence=evidence,
        ) if reasons else None

    def check_reviewer_pleasing(self, team_id: str) -> Optional[SpiralVerdict]:
        memory = self.tool.get_reviewer_memory(team_id)
        if memory is None:
            return None
        suspect = memory.reviewer_pleasing_signal(max_open_close_cycles=self.reopen_threshold)
        if not suspect:
            return None
        return SpiralVerdict(
            team_id=team_id,
            triggered=True,
            reasons=[
                f"reviewer-pleasing oscillation: {len(suspect)} concerns re-opened "
                f"> {self.reopen_threshold} cycles"
            ],
            evidence=[f"concern_id={cid}" for cid in suspect[:3]],
        )

    def check_capsule_breakage(self, team_id: str) -> Optional[SpiralVerdict]:
        capsules = self.tool.list_capsules(team_id=team_id)
        reasons: List[str] = []
        evidence: List[str] = []
        for cap in capsules:
            broken_count = sum(
                1 for h in cap.history if h.get("to") == "broken"
            )
            if broken_count >= 2:
                reasons.append(
                    f"capsule {cap.team_id}/{cap.capability} entered BROKEN "
                    f"{broken_count} times"
                )
                evidence.append(f"capsule_id={cap.capsule_id}")
        if not reasons:
            return None
        return SpiralVerdict(
            team_id=team_id,
            triggered=True,
            reasons=reasons,
            evidence=evidence,
        )

    def scan_team(self, team_id: str) -> SpiralVerdict:
        composite = SpiralVerdict(team_id=team_id, triggered=False)
        for check in (
            self.check_repeated_failures,
            self.check_reviewer_pleasing,
            self.check_capsule_breakage,
        ):
            result = check(team_id)
            if result and result.triggered:
                composite.triggered = True
                composite.reasons.extend(result.reasons)
                composite.evidence.extend(result.evidence)
        if composite.triggered:
            self.tool.emit_event(
                EventKind.DEATH_SPIRAL_DETECTED,
                team_id=team_id,
                payload={"reasons": composite.reasons, "evidence": composite.evidence},
                evidence=composite.evidence,
            )
            self.queue.file(
                title=f"death-spiral: {team_id}",
                team_id=team_id,
                details={"reasons": composite.reasons, "evidence": composite.evidence},
                evidence=composite.evidence,
            )
        return composite

    def scan_all(self, team_ids: Iterable[str]) -> List[SpiralVerdict]:
        return [self.scan_team(t) for t in team_ids]
