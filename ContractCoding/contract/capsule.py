"""InterfaceCapsule v2 — 4-layer + lifecycle state machine.

  L1 Tag       (≤200 tok) — name + one-line purpose + ≤3 key capabilities.
  L2 Interface (≤800 tok) — interface_def + ≥2 examples + assumptions + gotchas.
  L3 Artifacts (filesystem) — stub package + mock impl + smoke tests + MANIFEST.md.
  L4 Provenance (lazy)    — full margin annotation history; audit only.

Lifecycle:

   PROPOSED ──► DRAFT ──► LOCKED ──► (additive) LOCKED'
       │           │           │
       │           │           └─► EVOLVED (v2 fork)
       │           └─► BROKEN (smoke fail N times)
       └─► BROKEN
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional


L1_MAX_CHARS = 800
L2_MAX_CHARS = 3200


class CapsuleStatus(str, Enum):
    PROPOSED = "proposed"
    DRAFT = "draft"
    LOCKED = "locked"
    EVOLVED = "evolved"
    DEPRECATED = "deprecated"
    BROKEN = "broken"


ALLOWED_TRANSITIONS: Dict[CapsuleStatus, List[CapsuleStatus]] = {
    CapsuleStatus.PROPOSED: [CapsuleStatus.DRAFT, CapsuleStatus.BROKEN],
    CapsuleStatus.DRAFT: [CapsuleStatus.LOCKED, CapsuleStatus.BROKEN, CapsuleStatus.DRAFT],
    CapsuleStatus.LOCKED: [CapsuleStatus.LOCKED, CapsuleStatus.EVOLVED, CapsuleStatus.BROKEN],
    CapsuleStatus.EVOLVED: [CapsuleStatus.DEPRECATED],
    CapsuleStatus.DEPRECATED: [],
    CapsuleStatus.BROKEN: [CapsuleStatus.DRAFT],
}


@dataclass
class CapsuleTag:
    name: str
    one_line_purpose: str
    key_capabilities: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.name:
            errors.append("L1.name empty")
        if len(self.one_line_purpose) > 120:
            errors.append("L1.one_line_purpose exceeds 120 chars")
        if len(self.key_capabilities) > 3:
            errors.append("L1.key_capabilities must be ≤ 3 items")
        rendered = self.render()
        if len(rendered) > L1_MAX_CHARS:
            errors.append(f"L1 rendered size {len(rendered)} exceeds {L1_MAX_CHARS}")
        return errors

    def render(self) -> str:
        return (
            f"[{self.name}] {self.one_line_purpose}\n"
            f"capabilities: {', '.join(self.key_capabilities)}"
        )

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "one_line_purpose": self.one_line_purpose,
            "key_capabilities": list(self.key_capabilities),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "CapsuleTag":
        payload = dict(payload or {})
        return cls(
            name=str(payload.get("name", "")),
            one_line_purpose=str(payload.get("one_line_purpose", ""))[:120],
            key_capabilities=[str(v) for v in payload.get("key_capabilities", []) or []][:3],
        )


@dataclass
class ExecutableExample:
    name: str
    invocation: str
    expected: str
    pytest_path: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "invocation": self.invocation,
            "expected": self.expected,
            "pytest_path": self.pytest_path,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "ExecutableExample":
        payload = dict(payload or {})
        return cls(
            name=str(payload.get("name", "")),
            invocation=str(payload.get("invocation", "")),
            expected=str(payload.get("expected", "")),
            pytest_path=payload.get("pytest_path"),
        )


@dataclass
class CapsuleInterface:
    name: str
    interface_def: Dict[str, Any]
    examples: List[ExecutableExample] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    gotchas: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.name:
            errors.append("L2.name empty")
        if not self.interface_def:
            errors.append("L2.interface_def empty (need OpenAPI / type defs)")
        if len(self.examples) < 2:
            errors.append("L2.examples must have ≥ 2 executable examples")
        for idx, ex in enumerate(self.examples):
            if not ex.invocation or not ex.expected:
                errors.append(f"L2.examples[{idx}] incomplete")
        return errors

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "interface_def": dict(self.interface_def),
            "examples": [ex.to_record() for ex in self.examples],
            "assumptions": list(self.assumptions),
            "gotchas": list(self.gotchas),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "CapsuleInterface":
        payload = dict(payload or {})
        return cls(
            name=str(payload.get("name", "")),
            interface_def=dict(payload.get("interface_def", {}) or {}),
            examples=[
                ExecutableExample.from_mapping(v) for v in payload.get("examples", []) or []
            ],
            assumptions=[str(v) for v in payload.get("assumptions", []) or []],
            gotchas=[str(v) for v in payload.get("gotchas", []) or []],
        )


@dataclass
class CapsuleArtifacts:
    stub_package: str = ""
    mock_implementation: str = ""
    smoke_tests: str = ""
    manifest: str = ""
    contract_files: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "stub_package": self.stub_package,
            "mock_implementation": self.mock_implementation,
            "smoke_tests": self.smoke_tests,
            "manifest": self.manifest,
            "contract_files": list(self.contract_files),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "CapsuleArtifacts":
        payload = dict(payload or {})
        return cls(
            stub_package=str(payload.get("stub_package", "")),
            mock_implementation=str(payload.get("mock_implementation", "")),
            smoke_tests=str(payload.get("smoke_tests", "")),
            manifest=str(payload.get("manifest", "")),
            contract_files=[str(v) for v in payload.get("contract_files", []) or []],
        )


_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class SemVer:
    major: int = 1
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, raw: str) -> "SemVer":
        m = _SEMVER_RE.match((raw or "v1.0.0").strip())
        if not m:
            return cls(1, 0, 0)
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def render(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"

    def bump_minor(self) -> "SemVer":
        return SemVer(self.major, self.minor + 1, 0)

    def bump_major(self) -> "SemVer":
        return SemVer(self.major + 1, 0, 0)


@dataclass
class InterfaceCapsuleV2:
    capsule_id: str
    team_id: str
    capability: str
    version: str = "v1.0.0"
    status: CapsuleStatus = CapsuleStatus.PROPOSED
    tag: Optional[CapsuleTag] = None
    interface: Optional[CapsuleInterface] = None
    artifacts: CapsuleArtifacts = field(default_factory=CapsuleArtifacts)
    consumers: List[str] = field(default_factory=list)
    smoke_failures: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = CapsuleStatus(self.status)

    def validate_layer(self, layer: str) -> List[str]:
        errors: List[str] = []
        if layer == "L1":
            if self.tag is None:
                return ["L1.tag missing"]
            errors.extend(self.tag.validate())
        elif layer == "L2":
            if self.interface is None:
                return ["L2.interface missing"]
            errors.extend(self.interface.validate())
        elif layer == "L3":
            if not self.artifacts.stub_package:
                errors.append("L3.stub_package missing")
            if not self.artifacts.smoke_tests:
                errors.append("L3.smoke_tests missing")
            if not self.artifacts.manifest:
                errors.append("L3.manifest missing")
        else:
            errors.append(f"unknown layer {layer!r}")
        return errors

    def can_transition(self, target: CapsuleStatus) -> bool:
        return target in ALLOWED_TRANSITIONS.get(self.status, [])

    def to_record(self) -> Dict[str, Any]:
        return {
            "capsule_id": self.capsule_id,
            "team_id": self.team_id,
            "capability": self.capability,
            "version": self.version,
            "status": self.status.value,
            "tag": self.tag.to_record() if self.tag else None,
            "interface": self.interface.to_record() if self.interface else None,
            "artifacts": self.artifacts.to_record(),
            "consumers": list(self.consumers),
            "smoke_failures": int(self.smoke_failures),
            "history": list(self.history),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "InterfaceCapsuleV2":
        payload = dict(payload or {})
        return cls(
            capsule_id=str(payload.get("capsule_id", "")),
            team_id=str(payload.get("team_id", "")),
            capability=str(payload.get("capability", "")),
            version=str(payload.get("version", "v1.0.0")),
            status=CapsuleStatus(str(payload.get("status", "proposed"))),
            tag=CapsuleTag.from_mapping(payload["tag"]) if payload.get("tag") else None,
            interface=(
                CapsuleInterface.from_mapping(payload["interface"])
                if payload.get("interface")
                else None
            ),
            artifacts=CapsuleArtifacts.from_mapping(payload.get("artifacts", {}) or {}),
            consumers=[str(v) for v in payload.get("consumers", []) or []],
            smoke_failures=int(payload.get("smoke_failures", 0) or 0),
            history=list(payload.get("history", []) or []),
        )
