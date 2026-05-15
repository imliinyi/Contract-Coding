"""Skill cards loaded from progressive-disclosure skill folders.

Human-authored skill content lives under `ContractCoding/knowledge/skills/*`.
This module is only the runtime adapter: it parses `SKILL.md` frontmatter,
maps applicability keys to packet predicates, and exposes `SkillCard`s for
Inspector to pull into worker prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional


PacketLike = Any  # forward reference to ContextPacket — kept loose to avoid circular imports


ApplicabilityFn = Callable[[PacketLike], bool]


@dataclass
class SkillCard:
    """Declarative knowledge asset.

    Fields:
        skill_id     — stable identifier (used for de-dup + telemetry).
        title        — short human-readable label.
        applicable_roles — roles whose pass may pull this card.
        prompt_fragment  — text inserted into the user prompt when pulled.
        applicability_check — predicate over the packet; default = True.
        tags         — free-form labels for filtering (e.g. "auth",
                       "concurrency").
    """

    skill_id: str
    title: str
    applicable_roles: List[str] = field(default_factory=list)
    prompt_fragment: str = ""
    applicability_check: Optional[ApplicabilityFn] = None
    tags: List[str] = field(default_factory=list)
    source_path: str = ""

    def applies_to(self, role: str, packet: PacketLike) -> bool:
        if self.applicable_roles and role not in self.applicable_roles:
            return False
        if self.applicability_check is None:
            return True
        try:
            return bool(self.applicability_check(packet))
        except Exception:
            return False

    def to_record(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "title": self.title,
            "applicable_roles": list(self.applicable_roles),
            "prompt_fragment": self.prompt_fragment,
            "tags": list(self.tags),
            "source_path": self.source_path,
        }


@dataclass
class SkillLibrary:
    """Registry of `SkillCard`s keyed by `skill_id`.

    The library is a *memory-layer* primitive: workers pull from it via the
    Inspector pass; nothing else may write into it implicitly.
    """

    cards: Dict[str, SkillCard] = field(default_factory=dict)

    def register(self, card: SkillCard) -> None:
        self.cards[card.skill_id] = card

    def get(self, skill_id: str) -> Optional[SkillCard]:
        return self.cards.get(skill_id)

    def list_for(self, role: str, packet: PacketLike) -> List[SkillCard]:
        return [card for card in self.cards.values() if card.applies_to(role, packet)]

    def render_for(self, role: str, packet: PacketLike, *, max_cards: int = 4) -> str:
        applicable = self.list_for(role, packet)[:max_cards]
        if not applicable:
            return ""
        chunks: List[str] = ["=== Skill cards (pulled) ==="]
        for card in applicable:
            chunks.append(f"## {card.title} [{card.skill_id}]")
            if card.prompt_fragment:
                chunks.append(card.prompt_fragment.strip())
        return "\n".join(chunks)

    def to_record(self) -> Dict[str, Any]:
        return {"cards": [c.to_record() for c in self.cards.values()]}


# ---------------------------------------------------------------------------
# Applicability predicates
# ---------------------------------------------------------------------------


def _has_capsule_deps(packet: PacketLike) -> bool:
    deps = getattr(getattr(packet, "task", None), "capsule_dependencies", None)
    return bool(deps)


def _has_prior_failures(packet: PacketLike) -> bool:
    return bool(getattr(packet, "prior_failures", None))


def _has_artifacts(packet: PacketLike) -> bool:
    return bool(getattr(packet, "artifacts", None))


def _task_text(packet: PacketLike) -> str:
    task = getattr(packet, "task", None)
    if task is None:
        return ""
    parts: List[str] = [
        str(getattr(task, "task_id", "") or ""),
        str(getattr(task, "title", "") or ""),
        str(getattr(task, "goal", "") or ""),
        str(getattr(task, "output_format", "") or ""),
    ]
    parts.extend(str(v) for v in getattr(task, "boundaries", []) or [])
    parts.extend(str(v) for v in getattr(task, "tool_whitelist", []) or [])
    return " ".join(parts).lower()


def _task_has_any(packet: PacketLike, needles: List[str]) -> bool:
    text = _task_text(packet)
    return any(needle in text for needle in needles)


def _is_repair_task(packet: PacketLike) -> bool:
    return _task_has_any(
        packet,
        [
            "bug",
            "defect",
            "error",
            "exception",
            "fail",
            "failing",
            "fix",
            "lint",
            "regression",
            "repair",
            "smoke",
            "test",
            "traceback",
        ],
    )


def _is_multi_file_task(packet: PacketLike) -> bool:
    task = getattr(packet, "task", None)
    boundaries = getattr(task, "boundaries", []) if task is not None else []
    return len(boundaries or []) > 1 or _task_has_any(
        packet,
        ["api", "integration", "migration", "refactor", "wire"],
    )


def _has_tool_constraints(packet: PacketLike) -> bool:
    task = getattr(packet, "task", None)
    return bool(getattr(task, "tool_whitelist", None))


def _has_attempts(packet: PacketLike) -> bool:
    task = getattr(packet, "task", None)
    return bool(getattr(task, "attempts", 0) or 0)


def _has_prior_failures_or_attempts(packet: PacketLike) -> bool:
    return _has_prior_failures(packet) or _has_attempts(packet)


def _is_repair_task_or_has_artifacts(packet: PacketLike) -> bool:
    return _is_repair_task(packet) or _has_artifacts(packet)


APPLICABILITY_CHECKS: Dict[str, Optional[ApplicabilityFn]] = {
    "always": None,
    "artifacts": _has_artifacts,
    "capsule_deps": _has_capsule_deps,
    "multi_file": _is_multi_file_task,
    "prior_failures": _has_prior_failures,
    "prior_failures_or_attempts": _has_prior_failures_or_attempts,
    "repair": _is_repair_task,
    "repair_or_artifacts": _is_repair_task_or_has_artifacts,
    "tool_constraints": _has_tool_constraints,
}


# ---------------------------------------------------------------------------
# SKILL.md loading
# ---------------------------------------------------------------------------


DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "knowledge" / "skills"


def _parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        return ({}, text.strip())
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ({}, text.strip())
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return ({}, text.strip())

    meta: Dict[str, str] = {}
    for raw in lines[1:end_idx]:
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return (meta, "\n".join(lines[end_idx + 1 :]).strip())


def _parse_csv(value: str) -> List[str]:
    value = (value or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [
        item.strip().strip("\"'")
        for item in value.split(",")
        if item.strip()
    ]


def _slug_to_id(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "skill"


def _extract_runtime_prompt(body: str) -> str:
    marker = "## Runtime prompt"
    if marker not in body:
        return body.strip()
    tail = body.split(marker, 1)[1]
    lines = tail.splitlines()
    selected: List[str] = []
    for line in lines:
        if line.startswith("## ") and selected:
            break
        selected.append(line)
    return "\n".join(selected).strip()


def load_skill_cards(root: Optional[Path] = None) -> List[SkillCard]:
    """Load runtime cards from `*/SKILL.md` folders."""

    skills_root = root or DEFAULT_SKILLS_ROOT
    if not skills_root.exists():
        return []

    cards: List[SkillCard] = []
    for path in sorted(skills_root.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        if meta.get("runtime", "true").lower() in {"0", "false", "no"}:
            continue
        name = meta.get("name") or path.parent.name
        skill_id = meta.get("skill_id") or _slug_to_id(name)
        title = meta.get("title") or name.replace("_", " ").replace("-", " ").title()
        applicability = meta.get("applicability", "always")
        if applicability not in APPLICABILITY_CHECKS:
            raise ValueError(f"{path}: unknown applicability {applicability!r}")
        cards.append(
            SkillCard(
                skill_id=skill_id,
                title=title,
                applicable_roles=_parse_csv(meta.get("applicable_roles", "")),
                prompt_fragment=_extract_runtime_prompt(body),
                applicability_check=APPLICABILITY_CHECKS[applicability],
                tags=_parse_csv(meta.get("tags", "")),
                source_path=str(path),
            )
        )
    return cards


def default_skill_library() -> SkillLibrary:
    library = SkillLibrary()
    for card in load_skill_cards():
        library.register(card)
    return library
