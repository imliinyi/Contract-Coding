"""Independent LLM Reviewer with persistent memory.

Pulls its system prompt from `memory.PromptLibrary` (no hardcoded strings)
and uses persistent `ReviewerMemory` to detect reviewer-pleasing oscillation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.events import EventKind
from ..core.margin import AgentRole
from ..memory.prompts import PromptLibrary, default_prompt_library
from ..memory.reviewer_memory import AntiPattern, Concern, ReviewerMemory
from ..registry import RegistryTool
from ..worker.packet import ContextPacket
from ..worker.protocol import LLMPort, LLMRequest


__all__ = ["LLMReviewer", "make_pass"]


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


def _concern_id(description: str) -> str:
    return "concern:" + hashlib.sha1(description.strip().lower().encode("utf-8")).hexdigest()[:10]


def _antipattern_id(description: str) -> str:
    return "ap:" + hashlib.sha1(description.strip().lower().encode("utf-8")).hexdigest()[:10]


@dataclass
class LLMReviewer:
    tool: RegistryTool
    llm: LLMPort
    prompts: PromptLibrary = field(default_factory=default_prompt_library)
    severity_blocker_prefix: str = "blocker:"

    def run(self, packet: ContextPacket) -> ContextPacket:
        memory = (
            self.tool.get_reviewer_memory(packet.subcontract.team_id)
            or ReviewerMemory(team_id=packet.subcontract.team_id)
        )
        packet.reviewer_memory = memory

        prompt = self._build_prompt(packet, memory)
        result = self.llm.complete(
            LLMRequest(
                system_prompt=self.prompts.get(
                    AgentRole.REVIEWER, team_id=packet.subcontract.team_id
                ),
                user_prompt=prompt,
                role_hint="reviewer",
                temperature=0.0,
                max_tokens=2048,
            )
        )
        data = _try_json(result.text)

        new_concerns: List[str] = []
        for raw in data.get("concerns", []) or []:
            if not isinstance(raw, dict):
                continue
            description = str(raw.get("description") or "").strip()
            if not description:
                continue
            severity = str(raw.get("severity") or "minor").lower()
            evidence = [str(e) for e in raw.get("evidence", []) or []]

            cid = _concern_id(description)
            existing = self._find_concern(memory, cid)
            if existing:
                if existing.closed:
                    memory.reopen(cid, evidence)
                else:
                    existing.last_evidence = evidence
                    existing.open_count += 1
            else:
                concern = Concern(
                    concern_id=cid,
                    description=description,
                    closed=False,
                    open_count=1,
                    last_evidence=evidence,
                )
                memory.open_concerns.append(concern)

            label = description
            if severity == "blocker":
                label = f"{self.severity_blocker_prefix} {description}"
            new_concerns.append(label)

        for raw in data.get("anti_patterns", []) or []:
            if not isinstance(raw, dict):
                continue
            description = str(raw.get("description") or "").strip()
            if not description:
                continue
            memory.upsert_anti_pattern(
                AntiPattern(
                    pattern_id=_antipattern_id(description),
                    description=description,
                    seen_count=1,
                    example_evidence=[str(e) for e in raw.get("evidence", []) or []],
                )
            )

        for cid in data.get("closed", []) or []:
            memory.close(str(cid))

        for open_c in memory.open_concerns:
            label = open_c.description
            if label not in new_concerns:
                new_concerns.append(label)

        packet.reviewer_concerns = new_concerns
        self.tool.write_reviewer_memory(memory)

        self.tool.append_progress(
            packet.subcontract.team_id,
            task_id=packet.task.task_id,
            kind="reviewer",
            summary=f"{len(new_concerns)} concerns; {len(memory.seen_anti_patterns)} anti-patterns",
            payload={
                "concerns": new_concerns[:20],
                "anti_patterns": [p.pattern_id for p in memory.seen_anti_patterns],
            },
        )
        return packet

    def _find_concern(self, memory: ReviewerMemory, concern_id: str) -> Optional[Concern]:
        for c in memory.open_concerns + memory.closed_concerns:
            if c.concern_id == concern_id:
                return c
        return None

    def _build_prompt(self, packet: ContextPacket, memory: ReviewerMemory) -> str:
        artifact_dump = "\n\n".join(
            f"# {a.path}\n{a.content[:4000]}" for a in packet.artifacts
        ) or "(none)"
        capsules: List[Dict[str, Any]] = []
        for cap in packet.consumed_capsules:
            layer = packet.consumed_capsule_layers.get(cap.capsule_id, "L1")
            item: Dict[str, Any] = {
                "capsule": f"{cap.team_id}/{cap.capability}",
                "version": cap.version,
                "layer": layer,
                "tag": cap.tag.to_record() if cap.tag else None,
            }
            if layer in ("L2", "L3") and cap.interface:
                item["interface"] = cap.interface.to_record()
            capsules.append(item)
        history = {
            "open_concerns": [c.description for c in memory.open_concerns],
            "closed_concerns": [c.description for c in memory.closed_concerns[-10:]],
            "seen_anti_patterns": [p.description for p in memory.seen_anti_patterns[-10:]],
        }
        skills_block = ""
        reviewer_skills = packet.skill_fragments_by_role.get(AgentRole.REVIEWER.value, [])
        if reviewer_skills:
            skills_block = "\n=== Pulled skill cards ===\n" + "\n\n".join(reviewer_skills)
        return (
            f"Task: {packet.task.title}\n"
            f"Goal: {packet.task.goal}\n"
            f"Bounded context: {packet.bounded_context.purpose_one_liner}\n"
            f"Owned invariants: {packet.subcontract.working_paper.owned_invariants}\n"
            f"\n=== Capsules ===\n{json.dumps(capsules, ensure_ascii=False, indent=2)}\n"
            f"\n=== Reviewer memory ===\n{json.dumps(history, ensure_ascii=False, indent=2)}\n"
            f"{skills_block}\n"
            f"\n=== Artifacts ===\n{artifact_dump}\n"
        )


def make_pass(
    tool: RegistryTool,
    llm: LLMPort,
    *,
    prompts: Optional[PromptLibrary] = None,
) -> Any:
    """Return a callable compatible with `WorkerPipeline.reviewer`."""
    reviewer = LLMReviewer(tool=tool, llm=llm, prompts=prompts or default_prompt_library())
    return reviewer.run
