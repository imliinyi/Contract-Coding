"""Structured gate review parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List


@dataclass
class GateReviewVerdict:
    verdict: str
    block_reason: str = ""
    evidence: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    raw: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict in {"pass", "pass_with_risks"}

    @property
    def error(self) -> str:
        if self.accepted:
            return ""
        suffix = f": {self.block_reason}" if self.block_reason else ""
        return f"Gate review is {self.verdict}{suffix}"


class GateReviewParser:
    def parse(self, payload: Dict[str, Any], review_policy: Dict[str, Any]) -> GateReviewVerdict:
        candidate = payload.get("gate_review") or payload.get("review") or payload.get("verdict")
        raw = ""
        if isinstance(candidate, str):
            raw = candidate
            candidate = {"verdict": candidate}
        if not candidate:
            text = "\n".join(str(payload.get(key, "")) for key in ("thinking", "output") if payload.get(key))
            match = re.search(r"<gate_review>\s*(.*?)\s*</gate_review>", text, re.DOTALL)
            raw = match.group(1) if match else text
            try:
                candidate = json.loads(raw)
            except json.JSONDecodeError:
                if re.search(r"\bpass\b", text.lower()):
                    candidate = {"verdict": "pass", "evidence": [text[:1000]]}
                else:
                    candidate = {"verdict": "blocked", "block_reason": "unstructured_review"}
        if not isinstance(candidate, dict):
            candidate = {"verdict": "blocked", "block_reason": "invalid_review_payload"}
        allowed = set(review_policy.get("allowed_block_reasons", []) or [])
        verdict = str(candidate.get("verdict", "blocked")).strip().lower()
        if verdict not in {"pass", "pass_with_risks", "fail", "blocked"}:
            verdict = "blocked"
        reason = str(candidate.get("block_reason", "")).strip()
        if verdict in {"fail", "blocked"} and allowed and reason not in allowed:
            return GateReviewVerdict(
                verdict="pass_with_risks",
                block_reason="",
                evidence=[f"Out-of-layer block reason downgraded to risk: {reason or 'unspecified'}"],
                risks=[reason or "unspecified"],
                raw=raw,
            )
        return GateReviewVerdict(
            verdict=verdict,
            block_reason=reason,
            evidence=[str(value) for value in candidate.get("evidence", []) or []],
            risks=[str(value) for value in candidate.get("risks", []) or []],
            raw=raw,
        )

