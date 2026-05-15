"""Typed validation evidence records."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional


def digest_text(text: str, *, limit: int = 1200) -> str:
    clipped = text[:limit]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest} chars:{len(text)} preview:{clipped}"


@dataclass
class ValidationEvidence:
    evidence_id: str
    team_id: str
    work_id: str
    command: str
    passed: bool
    exit_code: int = 0
    stdout_digest: str = ""
    stderr_digest: str = ""
    artifact_refs: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        *,
        team_id: str,
        work_id: str,
        command: str,
        passed: bool,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        artifact_refs: Optional[List[str]] = None,
    ) -> "ValidationEvidence":
        return cls(
            evidence_id=f"evidence:{uuid.uuid4().hex[:12]}",
            team_id=team_id,
            work_id=work_id,
            command=command,
            passed=passed,
            exit_code=exit_code,
            stdout_digest=digest_text(stdout) if stdout else "",
            stderr_digest=digest_text(stderr) if stderr else "",
            artifact_refs=list(artifact_refs or []),
        )

    def ref(self) -> str:
        return self.evidence_id

    def to_record(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "team_id": self.team_id,
            "work_id": self.work_id,
            "command": self.command,
            "passed": bool(self.passed),
            "exit_code": int(self.exit_code),
            "stdout_digest": self.stdout_digest,
            "stderr_digest": self.stderr_digest,
            "artifact_refs": list(self.artifact_refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ValidationEvidence":
        payload = dict(payload or {})
        return cls(
            evidence_id=str(payload.get("evidence_id", "") or f"evidence:{uuid.uuid4().hex[:12]}"),
            team_id=str(payload.get("team_id", "")),
            work_id=str(payload.get("work_id", "")),
            command=str(payload.get("command", "")),
            passed=bool(payload.get("passed", False)),
            exit_code=int(payload.get("exit_code", 0) or 0),
            stdout_digest=str(payload.get("stdout_digest", "")),
            stderr_digest=str(payload.get("stderr_digest", "")),
            artifact_refs=[str(v) for v in payload.get("artifact_refs", []) or []],
            created_at=float(payload.get("created_at", time.time())),
        )
