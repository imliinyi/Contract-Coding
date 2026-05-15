#!/usr/bin/env python3
"""Validate ContractCoding skill folder metadata and runtime structure."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KNOWN_APPLICABILITY = {
    "always",
    "artifacts",
    "capsule_deps",
    "multi_file",
    "prior_failures",
    "prior_failures_or_attempts",
    "repair",
    "repair_or_artifacts",
    "tool_constraints",
}
REQUIRED_RUNTIME_FIELDS = {
    "skill_id",
    "title",
    "applicable_roles",
    "tags",
    "applicability",
}
WORKFLOW_SUMMARY_WORDS = {
    "breaks",
    "cite",
    "closes",
    "creates",
    "detects",
    "enforces",
    "generates",
    "implements",
    "loads",
    "maps",
    "prioritizes",
    "rejects",
    "requires",
    "validates",
}


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text

    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, "\n".join(lines[end + 1 :])


def is_runtime(meta: dict[str, str]) -> bool:
    return meta.get("runtime", "true").lower() not in {"0", "false", "no"}


def validate_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    errors: list[str] = []

    name = meta.get("name", "")
    description = meta.get("description", "")

    if not name:
        errors.append("missing name")
    elif not NAME_RE.match(name):
        errors.append("name must use lowercase letters, numbers, and hyphens")

    if not description:
        errors.append("missing description")
    elif not description.startswith("Use when"):
        errors.append("description must start with 'Use when'")
    if len(description) > 500:
        errors.append("description should be under 500 characters")
    if any(word in description.lower() for word in WORKFLOW_SUMMARY_WORDS):
        errors.append("description appears to summarize workflow instead of trigger")

    if is_runtime(meta):
        missing = sorted(REQUIRED_RUNTIME_FIELDS - set(meta))
        if missing:
            errors.append(f"missing runtime fields: {', '.join(missing)}")
        applicability = meta.get("applicability", "")
        if applicability and applicability not in KNOWN_APPLICABILITY:
            errors.append(f"unknown applicability: {applicability}")
        if "## Runtime prompt" not in body:
            errors.append("runtime skill must include '## Runtime prompt'")

    return [f"{path.relative_to(ROOT)}: {error}" for error in errors]


def main() -> int:
    errors: list[str] = []
    for path in sorted(ROOT.glob("*/SKILL.md")):
        errors.extend(validate_file(path))

    if errors:
        for error in errors:
            print(error)
        return 1

    print(f"Validated {len(list(ROOT.glob('*/SKILL.md')))} skill files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
