"""Worker passes 1, 2, 3, 5 (the Reviewer pass lives in `agents/reviewer.py`).

Each pass is a small callable class with one method `run(packet)`. They:
  - read from the packet for upstream context;
  - call the LLM (if needed) using a system prompt PULLED from
    `memory.PromptLibrary`;
  - InspectorPass additionally PULLS role-specific skill cards from
    `memory.SkillLibrary` into `packet.skill_fragments_by_role`;
  - write back to the packet;
  - emit a progress entry through the bound `RegistryTool`.

Passes are intentionally side-effect-explicit: anything that touches the
registry must go through `tool` so the actor + ACL is consistent.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..contract.capsule import InterfaceCapsuleV2
from ..contract.diff import ChangeSet, FileChange, sha256_text
from ..core.events import EventKind
from ..core.margin import AgentRole
from ..memory.ledgers import FailedHypothesis, TaskStatus
from ..memory.prompts import PromptLibrary, default_prompt_library
from ..memory.skills import SkillLibrary, default_skill_library
from ..registry import RegistryTool
from .packet import ContextPacket, SliceArtifact, SlicePlan, SliceVerdict
from .protocol import LLMPort, LLMRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _failure_fingerprint(packet: ContextPacket, error_class: str = "logic") -> str:
    parts = [
        packet.task.task_id,
        ",".join(sorted(packet.task.boundaries))[:64],
        error_class,
    ]
    raw = "|".join(parts)
    return "fp:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _skill_block_for(role: AgentRole, packet: ContextPacket) -> str:
    """Render the packet's pulled skill fragments as a labelled prompt block."""
    fragments = packet.skill_fragments_by_role.get(role.value, [])
    if not fragments:
        return ""
    return "\n=== Pulled skill cards ===\n" + "\n\n".join(fragments)


# ---------------------------------------------------------------------------
# Pass 1 — Planner
# ---------------------------------------------------------------------------


@dataclass
class PlannerPass:
    tool: RegistryTool
    llm: LLMPort
    prompts: PromptLibrary = field(default_factory=default_prompt_library)
    max_tokens: int = 2048

    def run(self, packet: ContextPacket) -> ContextPacket:
        prompt = self._build_prompt(packet)
        result = self.llm.complete(
            LLMRequest(
                system_prompt=self.prompts.get(
                    AgentRole.PLANNER, team_id=packet.subcontract.team_id
                ),
                user_prompt=prompt,
                role_hint="planner",
                temperature=0.1,
                max_tokens=self.max_tokens,
            )
        )
        data = _try_json(result.text)
        plan = SlicePlan(
            subtasks=list(data.get("subtasks", []) or []),
            open_questions=[str(q) for q in data.get("open_questions", []) or []],
            forbidden_patterns=[
                p
                for f in packet.prior_failures
                for p in f.forbidden_patterns
            ],
            raw_text=result.text,
        )
        packet.slice_plan = plan
        self.tool.append_progress(
            packet.subcontract.team_id,
            task_id=packet.task.task_id,
            kind="planner",
            summary=f"{len(plan.subtasks)} subtasks; {len(plan.open_questions)} open questions",
            payload={
                "open_questions": plan.open_questions,
                "subtask_titles": [s.get("title", "") for s in plan.subtasks],
            },
        )
        return packet

    def _build_prompt(self, packet: ContextPacket) -> str:
        capsule_summaries = "\n".join(
            f"- {c.team_id}/{c.capability} v{c.version}: "
            f"{c.tag.one_line_purpose if c.tag else ''}"
            for c in packet.consumed_capsules
        ) or "(none)"
        prior_summaries = "\n".join(
            f"- {f.fingerprint}: {f.what_was_tried[:80]} → {f.why_failed[:80]}"
            for f in packet.prior_failures
        ) or "(none)"
        return (
            f"Goal: {packet.task.goal}\n"
            f"Output format: {packet.task.output_format}\n"
            f"Boundaries: {packet.task.boundaries}\n"
            f"Declared writes: {getattr(packet.work_item, 'writes', []) if packet.work_item else []}\n"
            f"Validation commands: {getattr(packet.work_item, 'validation_commands', []) if packet.work_item else []}\n"
            f"Tool whitelist: {packet.task.tool_whitelist}\n"
            f"Bounded context purpose: {packet.bounded_context.purpose_one_liner}\n"
            f"\n--- Consumed capsules ---\n{capsule_summaries}\n"
            f"\n--- Prior failures (avoid) ---\n{prior_summaries}\n"
            f"{_skill_block_for(AgentRole.PLANNER, packet)}\n"
        )


# ---------------------------------------------------------------------------
# Pass 2 — Inspector
# ---------------------------------------------------------------------------


@dataclass
class InspectorPass:
    tool: RegistryTool
    skills: SkillLibrary = field(default_factory=default_skill_library)

    def run(self, packet: ContextPacket) -> ContextPacket:
        # Pull-based: only fetch what the task explicitly depends on.
        consumed: List[InterfaceCapsuleV2] = []
        for cap_ref in packet.task.capsule_dependencies:
            owner_team, capability = self._split_ref(cap_ref)
            cap = self.tool.get_capsule(owner_team, capability)
            if cap is None:
                blocker = f"missing capsule dependency: {cap_ref}"
                packet.notes.append(blocker)
                packet.blockers.append(blocker)
                continue
            consumed.append(cap)
            packet.consumed_capsule_layers[cap.capsule_id] = "L2"
        packet.consumed_capsules = consumed

        # Always include neighbour L1 tags (broad context, cheap).
        l1_neighbours = []
        for cap in self.tool.list_capsules():
            if cap.capsule_id in packet.consumed_capsule_layers:
                continue
            if cap.team_id == packet.subcontract.team_id:
                continue
            l1_neighbours.append(cap)
            packet.consumed_capsule_layers[cap.capsule_id] = "L1"
        packet.consumed_capsules.extend(l1_neighbours)

        # Failures matching this task fingerprint
        fp_prefix = packet.task.task_id
        prior = [
            f for f in self.tool.list_failures(packet.subcontract.team_id)
            if fp_prefix in f.related_task_ids
        ]
        packet.prior_failures = prior

        # PULL skill cards by downstream role. Keep role-specific fragments
        # separate so planner/reviewer/judge policies do not bleed into each
        # other.
        seen_skill_ids: set = set()
        rendered_by_role: Dict[str, List[str]] = {}
        for role in (
            AgentRole.PLANNER,
            AgentRole.IMPLEMENTER,
            AgentRole.REVIEWER,
            AgentRole.JUDGE,
        ):
            role_fragments: List[str] = []
            for card in self.skills.list_for(role.value, packet):
                seen_skill_ids.add(card.skill_id)
                fragment = card.prompt_fragment.strip()
                if not fragment:
                    continue
                role_fragments.append(f"[{card.skill_id}] {card.title}\n{fragment}")
            rendered_by_role[role.value] = role_fragments
        packet.skill_fragments_by_role = rendered_by_role

        self.tool.append_progress(
            packet.subcontract.team_id,
            task_id=packet.task.task_id,
            kind="inspector",
            summary=(
                f"pulled {len(consumed)} L2 capsules, "
                f"{len(l1_neighbours)} L1 neighbours, "
                f"{len(prior)} prior failures, "
                f"{len(seen_skill_ids)} skill cards"
            ),
            payload={
                "consumed": [c.capsule_id for c in consumed],
                "l1_neighbours": [c.capsule_id for c in l1_neighbours],
                "skills": list(seen_skill_ids),
                "skills_by_role": {
                    role: len(fragments)
                    for role, fragments in rendered_by_role.items()
                },
                "blockers": list(packet.blockers),
            },
        )
        return packet

    def _split_ref(self, ref: str) -> Any:
        if "/" in ref:
            head, tail = ref.split("/", 1)
            return (head, tail)
        if ":" in ref:
            head, tail = ref.split(":", 1)
            return (head, tail)
        return ("", ref)


# ---------------------------------------------------------------------------
# Pass 3 — Implementer
# ---------------------------------------------------------------------------


@dataclass
class ImplementerPass:
    tool: RegistryTool
    llm: LLMPort
    prompts: PromptLibrary = field(default_factory=default_prompt_library)
    max_tokens: int = 8192

    def run(self, packet: ContextPacket) -> ContextPacket:
        prompt = self._build_prompt(packet)
        result = self.llm.complete(
            LLMRequest(
                system_prompt=self.prompts.get(
                    AgentRole.IMPLEMENTER, team_id=packet.subcontract.team_id
                ),
                user_prompt=prompt,
                role_hint="implementer",
                temperature=0.2,
                max_tokens=self.max_tokens,
            )
        )
        data = _try_json(result.text)

        artifacts: List[SliceArtifact] = []
        for raw in data.get("artifacts", []) or []:
            if not isinstance(raw, dict):
                continue
            path = str(raw.get("path") or "").strip()
            content = str(raw.get("content") or "")
            if not path:
                continue
            artifacts.append(
                SliceArtifact(
                    path=path,
                    content=content,
                    intent=str(raw.get("intent") or ""),
                    is_test=bool(raw.get("is_test")),
                )
            )
        written_artifacts: List[SliceArtifact] = []
        change_set = ChangeSet(
            change_set_id=f"changes:{uuid.uuid4().hex[:12]}",
            team_id=packet.subcontract.team_id,
            work_id=getattr(packet.work_item, "work_id", packet.task.task_id),
            base_label="workspace-current",
        )
        for art in artifacts:
            normalised_path = self._normalise_artifact_path(art.path)
            if not normalised_path or not self._artifact_allowed(packet, normalised_path):
                reason = f"artifact outside declared writes: {art.path}"
                packet.blockers.append(reason)
                change_set.changes.append(
                    FileChange(
                        path=art.path,
                        status="rejected",
                        conflict=True,
                        conflict_reason=reason,
                    )
                )
                continue
            before = self.tool.read_workspace_text(packet.subcontract.team_id, normalised_path)
            before_sha = sha256_text(before)
            change = self.tool.write_workspace_text_checked(
                packet.subcontract.team_id,
                normalised_path,
                art.content,
                expected_sha256=before_sha,
            )
            change_set.changes.append(change)
            if change.conflict:
                packet.blockers.append(f"write conflict on {normalised_path}: {change.conflict_reason}")
                continue
            art.path = normalised_path
            written_artifacts.append(art)
        packet.change_set = change_set
        packet.artifacts = written_artifacts


        for d in data.get("decisions", []) or []:
            if not isinstance(d, dict):
                continue
            self.tool.append_decision(
                packet.subcontract.team_id,
                str(d.get("statement", "")),
                rationale=str(d.get("rationale", "")),
                uncertainty=float(data.get("uncertainty", 0.0) or 0.0),
            )

        self.tool.append_progress(
            packet.subcontract.team_id,
            task_id=packet.task.task_id,
            kind="implementer",
            summary=f"wrote {len(written_artifacts)} artifacts",
            payload={
                "files": [a.path for a in written_artifacts],
                "change_set": change_set.to_record(),
                "conflicts": [
                    c.to_record() for c in change_set.changes if c.conflict
                ],
                "uncertainty": float(data.get("uncertainty", 0.0) or 0.0),
            },
            uncertainty=float(data.get("uncertainty", 0.0) or 0.0),
        )
        return packet

    def _normalise_artifact_path(self, path: str) -> str:
        parts: List[str] = []
        for raw in path.replace("\\", "/").split("/"):
            if raw in ("", "."):
                continue
            if raw == "..":
                if not parts:
                    return ""
                parts.pop()
                continue
            parts.append(raw)
        return "/".join(parts)

    def _artifact_allowed(self, packet: ContextPacket, path: str) -> bool:
        work_item = packet.work_item
        declared = [self._normalise_artifact_path(p) for p in getattr(work_item, "writes", []) or []]
        declared = [p for p in declared if p]
        if not declared:
            return True
        for allowed in declared:
            if path == allowed:
                return True
            if path.startswith(allowed.rstrip("/") + "/"):
                return True
        return False

    def _build_prompt(self, packet: ContextPacket) -> str:
        plan = packet.slice_plan or SlicePlan()
        capsule_payload: List[Dict[str, Any]] = []
        for cap in packet.consumed_capsules:
            layer = packet.consumed_capsule_layers.get(cap.capsule_id, "L1")
            entry = {
                "capsule": f"{cap.team_id}/{cap.capability}",
                "version": cap.version,
                "layer": layer,
                "tag": cap.tag.to_record() if cap.tag else None,
            }
            if layer in ("L2", "L3") and cap.interface:
                entry["interface"] = cap.interface.to_record()
            if layer == "L3":
                entry["artifacts"] = cap.artifacts.to_record()
            capsule_payload.append(entry)

        return (
            f"Task: {packet.task.title}\n"
            f"Goal: {packet.task.goal}\n"
            f"Boundaries: {packet.task.boundaries}\n"
            f"Declared writes: {getattr(packet.work_item, 'writes', []) if packet.work_item else []}\n"
            f"Validation commands: {getattr(packet.work_item, 'validation_commands', []) if packet.work_item else []}\n"
            "Only emit artifacts whose path is listed in Declared writes or is below one of those declared prefixes. "
            "If Declared writes lists files, emit those exact file paths.\n"
            f"Subtasks: {json.dumps(plan.subtasks, ensure_ascii=False)}\n"
            f"Open questions: {plan.open_questions}\n"
            f"Forbidden patterns: {plan.forbidden_patterns}\n"
            f"Working paper invariants: {packet.subcontract.working_paper.owned_invariants}\n"
            f"Capsules:\n{json.dumps(capsule_payload, ensure_ascii=False, indent=2)}\n"
            f"{_skill_block_for(AgentRole.IMPLEMENTER, packet)}\n"
        )


# ---------------------------------------------------------------------------
# Pass 5 — Judge
# ---------------------------------------------------------------------------


@dataclass
class JudgePass:
    """Aggregates reviewer concerns + smoke results + invariants → verdict."""

    tool: RegistryTool
    prompts: PromptLibrary = field(default_factory=default_prompt_library)

    def run(
        self,
        packet: ContextPacket,
        *,
        smoke_passed: Optional[bool] = None,
    ) -> ContextPacket:
        blockers: List[str] = []
        reasons: List[str] = []

        if not packet.artifacts:
            blockers.append("no artifacts produced")
        missing_declared = self._missing_declared_artifacts(packet)
        if missing_declared:
            blockers.append(
                "missing declared artifacts: " + ", ".join(missing_declared[:12])
            )
        blockers.extend(packet.blockers)
        if smoke_passed is False:
            blockers.append("smoke tests failing")
        if smoke_passed is True:
            reasons.append("smoke tests pass")

        for concern in packet.reviewer_concerns:
            if concern.lower().startswith("blocker"):
                blockers.append(concern)
            else:
                reasons.append(concern)

        if packet.reviewer_memory is not None:
            suspect = packet.reviewer_memory.reviewer_pleasing_signal()
            if suspect:
                blockers.append(
                    f"reviewer-pleasing pattern detected: concerns {suspect[:3]} "
                    f"reopened > threshold; coordinator escalation required"
                )

        approved = not blockers
        verdict = SliceVerdict(
            approved=approved,
            reasons=reasons,
            blockers=blockers,
            reviewer_concerns=list(packet.reviewer_concerns),
            smoke_passed=smoke_passed,
            fingerprint=_failure_fingerprint(packet),
        )
        packet.verdict = verdict

        if approved:
            self.tool.set_task_status(packet.subcontract.team_id, packet.task.task_id, TaskStatus.DONE)
            self.tool.emit_event(
                EventKind.SLICE_VERIFIED,
                team_id=packet.subcontract.team_id,
                payload={"task_id": packet.task.task_id, "reasons": reasons},
            )
        else:
            packet.task.status = TaskStatus.BLOCKED
            self.tool.emit_event(
                EventKind.SLICE_REJECTED,
                team_id=packet.subcontract.team_id,
                payload={"task_id": packet.task.task_id, "blockers": blockers},
            )
            failure = FailedHypothesis(
                fingerprint=verdict.fingerprint or _failure_fingerprint(packet),
                what_was_tried=packet.task.goal,
                why_failed="; ".join(blockers)[:500],
                forbidden_patterns=[],
                related_task_ids=[packet.task.task_id],
            )
            self.tool.append_failure(packet.subcontract.team_id, failure)
            packet.task.attempts += 1
            self.tool.upsert_task(packet.subcontract.team_id, packet.task)

        self.tool.append_progress(
            packet.subcontract.team_id,
            task_id=packet.task.task_id,
            kind="judge",
            summary=f"verdict={'approve' if approved else 'reject'}",
            payload={
                "blockers": blockers,
                "reasons": reasons,
                "smoke_passed": smoke_passed,
            },
        )
        return packet

    def _missing_declared_artifacts(self, packet: ContextPacket) -> List[str]:
        work_item = packet.work_item
        declared = [
            self._normalise_path(path)
            for path in getattr(work_item, "writes", []) or []
        ]
        declared = [path for path in declared if path]
        if not declared:
            return []
        actual = {self._normalise_path(art.path) for art in packet.artifacts}
        missing: List[str] = []
        for path in declared:
            if self._looks_like_file(path):
                if path not in actual:
                    missing.append(path)
                continue
            prefix = path.rstrip("/") + "/"
            if not any(art_path.startswith(prefix) for art_path in actual):
                missing.append(path)
        return missing

    def _normalise_path(self, path: str) -> str:
        parts: List[str] = []
        for raw in str(path).replace("\\", "/").split("/"):
            if raw in ("", "."):
                continue
            if raw == "..":
                if not parts:
                    return ""
                parts.pop()
                continue
            parts.append(raw)
        return "/".join(parts)

    def _looks_like_file(self, path: str) -> bool:
        leaf = path.rsplit("/", 1)[-1]
        return "." in leaf
