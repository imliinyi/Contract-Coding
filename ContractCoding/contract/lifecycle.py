"""Capsule lifecycle state machine + executable gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..core.margin import MarginAnnotation
from .capsule import (
    ALLOWED_TRANSITIONS,
    CapsuleStatus,
    InterfaceCapsuleV2,
    SemVer,
)


class CapsuleTransitionError(ValueError):
    """Raised when an illegal capsule state transition is attempted."""


@dataclass
class TransitionResult:
    ok: bool
    from_status: CapsuleStatus
    to_status: CapsuleStatus
    errors: List[str]
    new_version: Optional[str] = None


def advance(
    capsule: InterfaceCapsuleV2,
    target: CapsuleStatus,
    *,
    margin: MarginAnnotation,
    reason: str = "",
    breaking_change: bool = False,
) -> TransitionResult:
    if not capsule.can_transition(target):
        raise CapsuleTransitionError(
            f"illegal transition {capsule.status.value} → {target.value}; "
            f"allowed: {[t.value for t in ALLOWED_TRANSITIONS.get(capsule.status, [])]}"
        )

    errors: List[str] = []
    new_version: Optional[str] = None

    if target == CapsuleStatus.DRAFT:
        errors.extend(capsule.validate_layer("L1"))
        errors.extend(capsule.validate_layer("L2"))
        errors.extend(capsule.validate_layer("L3"))

    elif target == CapsuleStatus.LOCKED:
        errors.extend(capsule.validate_layer("L1"))
        errors.extend(capsule.validate_layer("L2"))
        errors.extend(capsule.validate_layer("L3"))
        if capsule.status == CapsuleStatus.DRAFT and not capsule.consumers:
            errors.append("cannot LOCK capsule without ≥1 declared consumer")
        if capsule.status == CapsuleStatus.LOCKED:
            new_version = SemVer.parse(capsule.version).bump_minor().render()

    elif target == CapsuleStatus.EVOLVED:
        if not breaking_change:
            errors.append("LOCKED → EVOLVED requires breaking_change=True")
        new_version = SemVer.parse(capsule.version).bump_major().render()

    if errors:
        return TransitionResult(
            ok=False,
            from_status=capsule.status,
            to_status=capsule.status,
            errors=errors,
        )

    previous = capsule.status
    capsule.status = target
    if new_version:
        capsule.version = new_version
    capsule.history.append(
        {
            "from": previous.value,
            "to": target.value,
            "version": capsule.version,
            "reason": reason,
            "margin": margin.to_record(),
        }
    )
    return TransitionResult(
        ok=True,
        from_status=previous,
        to_status=target,
        errors=[],
        new_version=new_version,
    )


def record_smoke_failure(capsule: InterfaceCapsuleV2, *, threshold: int = 3) -> bool:
    capsule.smoke_failures += 1
    return capsule.smoke_failures >= threshold


def reset_smoke_failures(capsule: InterfaceCapsuleV2) -> None:
    capsule.smoke_failures = 0
