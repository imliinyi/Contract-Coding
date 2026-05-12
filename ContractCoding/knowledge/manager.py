"""Progressive skill router for Runtime V5.

Runtime V5 keeps a small in-code fallback catalog, but the preferred skill
source is a visible Agent-Skills-style directory under ``knowledge/skills``.
This makes plan/code/test/repair behavior auditable instead of hiding it in a
large prompt blob.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ContractCoding.knowledge.builtin_skills import BUILTIN_SKILLS


SKILLS_DIR = Path(__file__).with_name("skills")


class SkillRouter:
    def select(self, kind: str, slice_id: str = "") -> list[str]:
        if kind == "plan":
            return ["planning_product_kernel", "managed_feature_team_coordination", "feature_slice_design", "interface_contract_authoring", "interface_capsule_handshake"]
        if kind in {"capsule", "interface"}:
            return [
                "managed_feature_team_coordination",
                "interface_contract_authoring",
                "interface_capsule_handshake",
                "judge_contract_verification",
                "evidence_submission_protocol",
            ]
        if kind == "acceptance":
            return [
                "acceptance_test_authoring",
                "dependency_interface_consumption",
                "code_test_slice",
                "tool_use_protocol",
                "evidence_submission_protocol",
                "judge_contract_verification",
            ]
        if kind == "judge":
            return ["code_test_slice", "judge_contract_verification", "evidence_submission_protocol"]
        if kind == "repair":
            return [
                "repair_transaction",
                "dependency_interface_consumption",
                "code_generation_slice",
                "code_test_slice",
                "tool_use_protocol",
                "evidence_submission_protocol",
                "judge_contract_verification",
            ]
        if kind == "replan":
            return ["replan_failure_cluster", "planning_product_kernel", "feature_slice_design", "interface_contract_authoring", "interface_capsule_handshake"]

        base = [
            "managed_feature_team_coordination",
            "feature_slice_design",
            "dependency_interface_consumption",
            "interface_contract_authoring",
            "code_generation_slice",
            "code_test_slice",
            "tool_use_protocol",
            "evidence_submission_protocol",
        ]
        if slice_id == "package_surface":
            return ["interface_contract_authoring", "code_generation_slice", "tool_use_protocol", "evidence_submission_protocol"]
        if slice_id in {"public_interface", "persistence_flow", "planning_intelligence"}:
            return base
        return base


class ContextManager:
    def __init__(self, *_, **__):
        self.router = SkillRouter()
        self._skills = _load_skills()

    def skills_for(self, kind: str, slice_id: str = "") -> list[dict[str, Any]]:
        selected = set(self.router.select(kind, slice_id=slice_id))
        return [skill for skill in self._skills if skill["name"] in selected]

    def skills_for_item(self, item: Any, feature_slice: Any | None = None) -> list[dict[str, Any]]:
        kind = (
            "capsule"
            if getattr(item, "kind", "") in {"capsule", "interface"}
            else (
                "repair" if getattr(item, "kind", "") == "repair" else (
                    "acceptance" if getattr(item, "kind", "") == "acceptance" else "worker"
                )
            )
        )
        slice_id = getattr(feature_slice, "id", "") or getattr(item, "slice_id", "")
        return self.skills_for(kind, slice_id=slice_id)


def _load_skills() -> list[dict[str, Any]]:
    by_name = {str(skill.get("name")): dict(skill) for skill in BUILTIN_SKILLS}
    for skill_file in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        parsed = _parse_skill_file(skill_file)
        if not parsed.get("name"):
            continue
        base = by_name.get(parsed["name"], {})
        merged = {**base, **parsed}
        if "checklist" not in merged and "must" in parsed:
            merged["checklist"] = parsed["must"]
        if "forbidden" not in merged and "avoid" in parsed:
            merged["forbidden"] = parsed["avoid"]
        by_name[parsed["name"]] = merged
    ordered_names = [str(skill.get("name")) for skill in BUILTIN_SKILLS]
    extra_names = sorted(name for name in by_name if name not in ordered_names)
    return [by_name[name] for name in [*ordered_names, *extra_names] if name in by_name]


def _parse_skill_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    frontmatter: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip().strip("'\"")
            body = parts[2]
    must = _section_bullets(body, "Must")
    avoid = _section_bullets(body, "Avoid")
    return {
        "name": frontmatter.get("name", path.parent.name),
        "summary": frontmatter.get("description", ""),
        "description": frontmatter.get("description", ""),
        "checklist": must,
        "forbidden": avoid,
        "must": must,
        "avoid": avoid,
        "source_path": str(path),
    }


def _section_bullets(body: str, heading: str) -> list[str]:
    lines = body.splitlines()
    bullets: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            capture = stripped.strip("# ").lower() == heading.lower()
            continue
        if capture and stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return bullets
