"""Interface Steward — owns capsule artifacts.

Responsibilities:
  1. Translate a DRAFT capsule (L1 + L2) into L3 artifacts on disk.
  2. Publish the capsule into the registry, advancing PROPOSED → DRAFT.
  3. Re-run smoke tests on demand; record failures.
  4. Lock the capsule once at least one consumer has declared a dependency.

Deterministic + offline: every artifact is rendered from capsule data, no
LLM calls. That keeps publishing reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import textwrap
from typing import Any, List, Optional

from ..contract.capsule import (
    CapsuleArtifacts,
    CapsuleStatus,
    CapsuleTag,
    InterfaceCapsuleV2,
)
from ..contract.lifecycle import TransitionResult
from ..core.margin import AgentRole
from ..registry import RegistryTool


_IDENT_RE = re.compile(r"[^A-Za-z0-9_]+")


def _to_python_ident(name: str) -> str:
    cleaned = _IDENT_RE.sub("_", name).strip("_")
    if not cleaned:
        cleaned = "capsule"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def _safe_repr(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


@dataclass
class SmokeResult:
    ok: bool
    passed: int
    failed: int
    log: str


@dataclass
class StewardResult:
    capsule: InterfaceCapsuleV2
    artifacts: CapsuleArtifacts
    transition: Optional[TransitionResult] = None
    smoke: Optional[SmokeResult] = None


class InterfaceSteward:
    """Per-team capsule custodian."""

    def __init__(self, tool: RegistryTool):
        if tool.actor.role != AgentRole.STEWARD:
            raise ValueError("InterfaceSteward requires a STEWARD-bound RegistryTool")
        self.tool = tool
        self.team_id = tool.actor.team_id

    def publish(
        self,
        capsule: InterfaceCapsuleV2,
        *,
        target_status: CapsuleStatus = CapsuleStatus.DRAFT,
        run_smoke: bool = True,
        evidence: Optional[List[str]] = None,
    ) -> StewardResult:
        if capsule.team_id != self.team_id:
            raise ValueError(
                f"steward bound to team {self.team_id!r} but capsule belongs to {capsule.team_id!r}"
            )

        l1_errors = capsule.validate_layer("L1")
        l2_errors = capsule.validate_layer("L2")
        if l1_errors or l2_errors:
            raise ValueError(
                f"capsule fails L1/L2 validation: L1={l1_errors} L2={l2_errors}"
            )

        artifacts = self._render_artifacts(capsule)
        capsule.artifacts = artifacts

        smoke: Optional[SmokeResult] = None
        if run_smoke:
            smoke = self.run_smoke(capsule)
            if not smoke.ok:
                self.tool.record_capsule_smoke_failure(
                    capsule.team_id,
                    capsule.capability,
                    evidence=[f"smoke log:\n{smoke.log[-1000:]}"],
                    reason="steward smoke run failed",
                )
                return StewardResult(capsule=capsule, artifacts=artifacts, smoke=smoke)

        transition = self.tool.publish_capsule(
            capsule,
            target_status=target_status,
            reason="steward publish",
            evidence=evidence or [f"smoke ok={smoke.ok if smoke else 'skipped'}"],
        )
        return StewardResult(
            capsule=capsule, artifacts=artifacts, transition=transition, smoke=smoke
        )

    # ------------------------------------------------------------ artifacts

    def _render_artifacts(self, capsule: InterfaceCapsuleV2) -> CapsuleArtifacts:
        capability = capsule.capability
        team = capsule.team_id
        base_rel = f"contracts/{capability}"
        stub_rel = f"{base_rel}/stub"
        mock_rel = f"{base_rel}/mock"
        smoke_rel = f"{base_rel}/smoke"
        manifest_rel = f"{base_rel}/MANIFEST.md"

        self.tool.write_workspace_text(team, f"{stub_rel}/__init__.py", self._render_stub_init(capsule))
        self.tool.write_workspace_text(team, f"{stub_rel}/types.py", self._render_stub_types(capsule))
        self.tool.write_workspace_text(team, f"{mock_rel}/__init__.py", self._render_mock(capsule))
        self.tool.write_workspace_text(team, f"{smoke_rel}/__init__.py", "")
        self.tool.write_workspace_text(
            team, f"{smoke_rel}/test_examples.py", self._render_smoke_tests(capsule)
        )
        self.tool.write_workspace_text(team, manifest_rel, self._render_manifest(capsule))

        contract_files: List[str] = []
        if capsule.interface and capsule.interface.interface_def:
            spec_rel = f"{base_rel}/interface_def.json"
            self.tool.write_workspace_text(
                team,
                spec_rel,
                json.dumps(capsule.interface.interface_def, ensure_ascii=False, sort_keys=True, indent=2),
            )
            contract_files.append(f"workspace/{team}/{spec_rel}")
        examples_rel = f"{base_rel}/examples.json"
        examples_payload = (
            [ex.to_record() for ex in capsule.interface.examples] if capsule.interface else []
        )
        self.tool.write_workspace_text(
            team,
            examples_rel,
            json.dumps(examples_payload, ensure_ascii=False, sort_keys=True, indent=2),
        )
        contract_files.append(f"workspace/{team}/{examples_rel}")

        return CapsuleArtifacts(
            stub_package=f"workspace/{team}/{stub_rel}",
            mock_implementation=f"workspace/{team}/{mock_rel}",
            smoke_tests=f"workspace/{team}/{smoke_rel}",
            manifest=f"workspace/{team}/{manifest_rel}",
            contract_files=contract_files,
        )

    def _render_stub_init(self, capsule: InterfaceCapsuleV2) -> str:
        purpose = capsule.tag.one_line_purpose if capsule.tag else ""
        return textwrap.dedent(
            f'''\
            """Auto-generated stub for capsule {capsule.capability} owned by team {capsule.team_id}.

            Purpose: {purpose}

            DO NOT EDIT — regenerated by InterfaceSteward on every publish.
            """

            from .types import *  # noqa: F401,F403


            __capsule_id__ = "{capsule.capsule_id}"
            __capsule_team__ = "{capsule.team_id}"
            __capsule_capability__ = "{capsule.capability}"
            __capsule_version__ = "{capsule.version}"
            __capsule_status__ = "{capsule.status.value}"
            '''
        )

    def _render_stub_types(self, capsule: InterfaceCapsuleV2) -> str:
        interface_def = (
            capsule.interface.interface_def if capsule.interface else {}
        )
        lines = [
            '"""Type stubs derived from CapsuleInterface.interface_def."""',
            "from __future__ import annotations",
            "",
            "from typing import Any, Dict, List, Optional, TypedDict",
            "",
            "",
        ]
        if not interface_def:
            lines.append("# (interface_def empty — fill in OpenAPI / type definitions)")
            return "\n".join(lines) + "\n"

        for name, schema in interface_def.items():
            cls_name = _to_python_ident(name).capitalize()
            lines.append(f"class {cls_name}(TypedDict, total=False):")
            if isinstance(schema, dict) and schema:
                for field_name, field_type in schema.items():
                    py_field = _to_python_ident(field_name)
                    py_type = self._py_annotation(field_type)
                    lines.append(f"    {py_field}: {py_type}")
            else:
                lines.append("    pass")
            lines.append("")
        return "\n".join(lines)

    def _py_annotation(self, raw: Any) -> str:
        if isinstance(raw, str):
            mapping = {
                "string": "str",
                "str": "str",
                "int": "int",
                "integer": "int",
                "float": "float",
                "number": "float",
                "bool": "bool",
                "boolean": "bool",
                "list": "List[Any]",
                "array": "List[Any]",
                "dict": "Dict[str, Any]",
                "object": "Dict[str, Any]",
                "any": "Any",
            }
            return mapping.get(raw.lower(), "Any")
        return "Any"

    def _render_mock(self, capsule: InterfaceCapsuleV2) -> str:
        cap_ident = _to_python_ident(capsule.capability)
        examples = capsule.interface.examples if capsule.interface else []
        body_lines: List[str] = [
            f'"""Deterministic mock for capsule {capsule.capability}."""',
            "from __future__ import annotations",
            "",
            "from typing import Any, Dict",
            "",
            "# Hand-rolled examples wired below; downstream teams should depend on this",
            "# mock via `from <stub_path>.mock import <handler>`.",
            "",
        ]
        if not examples:
            body_lines.append(f"def {cap_ident}(*args: Any, **kwargs: Any) -> Dict[str, Any]:")
            body_lines.append('    return {"_mock": True}')
            return "\n".join(body_lines) + "\n"

        body_lines.append("EXAMPLES: Dict[str, Dict[str, Any]] = {")
        for ex in examples:
            ex_ident = _to_python_ident(ex.name) or "example"
            body_lines.append(
                f"    {json.dumps(ex_ident)}: {{\"invocation\": {json.dumps(ex.invocation)}, "
                f"\"expected\": {json.dumps(ex.expected)}}},"
            )
        body_lines.append("}")
        body_lines.append("")
        body_lines.append(f"def {cap_ident}_lookup(example_name: str) -> Dict[str, Any]:")
        body_lines.append("    return EXAMPLES.get(example_name, {})")
        return "\n".join(body_lines) + "\n"

    def _render_smoke_tests(self, capsule: InterfaceCapsuleV2) -> str:
        examples = capsule.interface.examples if capsule.interface else []
        lines: List[str] = [
            '"""Smoke tests auto-generated from CapsuleInterface.examples."""',
            "from __future__ import annotations",
            "",
            "import json",
            "",
            "EXAMPLES = " + json.dumps(
                [ex.to_record() for ex in examples], ensure_ascii=False, indent=2
            ),
            "",
        ]
        if not examples:
            lines.append("def test_capsule_has_examples():")
            lines.append(
                f"    assert False, "
                f"{json.dumps(f'capsule {capsule.capability} declared 0 executable examples')}"
            )
            return "\n".join(lines) + "\n"

        for idx, ex in enumerate(examples):
            slug = _to_python_ident(ex.name) or f"example_{idx}"
            lines.append(f"def test_{slug}():")
            lines.append("    spec = EXAMPLES[" + str(idx) + "]")
            lines.append("    assert spec[\"invocation\"], spec")
            lines.append("    assert spec[\"expected\"], spec")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _render_manifest(self, capsule: InterfaceCapsuleV2) -> str:
        tag = capsule.tag or CapsuleTag(name=capsule.capability, one_line_purpose="")
        examples = capsule.interface.examples if capsule.interface else []
        assumptions = capsule.interface.assumptions if capsule.interface else []
        gotchas = capsule.interface.gotchas if capsule.interface else []
        lines: List[str] = [
            f"# {tag.name} — {capsule.team_id}",
            "",
            f"**Capability:** `{capsule.capability}`  ",
            f"**Version:** `{capsule.version}`  ",
            f"**Status:** `{capsule.status.value}`  ",
            f"**Owner team:** `{capsule.team_id}`  ",
            "",
            "## Purpose",
            "",
            tag.one_line_purpose or "(none)",
            "",
            "## Key capabilities",
            "",
        ]
        for cap in tag.key_capabilities:
            lines.append(f"- {cap}")
        if not tag.key_capabilities:
            lines.append("- (none declared)")
        lines.append("")
        lines.append("## Executable examples")
        lines.append("")
        if examples:
            for ex in examples:
                lines.append(f"### {ex.name}")
                lines.append("")
                lines.append("```")
                lines.append(ex.invocation)
                lines.append("# expected:")
                lines.append(ex.expected)
                lines.append("```")
                lines.append("")
        else:
            lines.append("(none)")
            lines.append("")
        lines.append("## Assumptions")
        lines.append("")
        for a in assumptions or ["(none)"]:
            lines.append(f"- {a}")
        lines.append("")
        lines.append("## Gotchas")
        lines.append("")
        for g in gotchas or ["(none)"]:
            lines.append(f"- {g}")
        lines.append("")
        lines.append("## Consumers")
        lines.append("")
        for c in capsule.consumers or ["(none yet)"]:
            lines.append(f"- {c}")
        lines.append("")
        return "\n".join(lines)

    # ----------------------------------------------------------------- smoke

    def run_smoke(self, capsule: InterfaceCapsuleV2) -> SmokeResult:
        smoke_path = capsule.artifacts.smoke_tests
        if not smoke_path:
            return SmokeResult(ok=False, passed=0, failed=0, log="no smoke_tests path")
        full_smoke = os.path.join(self.tool.backend.root, smoke_path)
        if not os.path.exists(full_smoke):
            return SmokeResult(
                ok=False, passed=0, failed=0, log=f"smoke path missing: {full_smoke}"
            )

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", full_smoke],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            log = stdout + ("\n--stderr--\n" + stderr if stderr else "")
            ok = result.returncode == 0
            passed = stdout.count(" passed")
            failed = stdout.count(" failed")
            return SmokeResult(ok=ok, passed=passed, failed=failed, log=log)
        except FileNotFoundError as exc:
            return SmokeResult(
                ok=False, passed=0, failed=0, log=f"pytest not available: {exc}"
            )
        except subprocess.TimeoutExpired:
            return SmokeResult(
                ok=False,
                passed=0,
                failed=0,
                log="smoke run timed out (>120s)",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return SmokeResult(
                ok=False, passed=0, failed=0, log=f"smoke run errored: {exc!r}"
            )
