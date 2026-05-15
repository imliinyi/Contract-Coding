"""WorkerPipeline: glue for the 5-pass execution.

Order of operations:
  1. Inspector (pulls context + capsules)
  2. Planner   (decomposes goal)
  3. Implementer (writes artifacts)
  4. Reviewer  (independent LLM, see `..reviewer.LLMReviewer`)
  5. Judge     (aggregates verdict)

Failure handling:
  - If Inspector cannot resolve a capsule_dependency → escalate, set task
    to BLOCKED, return rejection.
  - If Implementer produces no artifacts → judge will reject; failure logged.
  - If Reviewer raises mid-call → judge sees no concerns; we record this in
    notes and refuse to approve.

The pipeline is single-threaded by design; concurrency between teams is
provided by the Coordinator, not here. This matches the AI Co-Mathematician
asynchronous-steering pattern: each team runs its slice, the coordinator
polls events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..core.events import EventKind
from ..memory.ledgers import TaskStatus
from ..memory.prompts import PromptLibrary, default_prompt_library
from ..memory.skills import SkillLibrary, default_skill_library
from ..registry import RegistryTool
from .packet import ContextPacket, SliceVerdict
from .passes import (
    ImplementerPass,
    InspectorPass,
    JudgePass,
    PlannerPass,
)
from .protocol import LLMPort


# ReviewerPass is provided by `..reviewer`; we accept any callable to avoid
# a circular import.
ReviewerCallable = Callable[[ContextPacket], ContextPacket]


@dataclass
class PipelineConfig:
    smoke_runner: Optional[Callable[[ContextPacket], Optional[bool]]] = None
    fail_fast_on_blocked: bool = True
    require_validation: bool = True
    planner_max_tokens: int = 2048
    implementer_max_tokens: int = 8192


@dataclass
class PipelineResult:
    packet: ContextPacket
    verdict: SliceVerdict
    notes: List[str] = field(default_factory=list)


class WorkerPipeline:
    """Compose 5 passes with cross-cut event emission."""

    def __init__(
        self,
        tool: RegistryTool,
        llm: LLMPort,
        reviewer: ReviewerCallable,
        *,
        control_tool: Optional[RegistryTool] = None,
        planner_tool: Optional[RegistryTool] = None,
        inspector_tool: Optional[RegistryTool] = None,
        implementer_tool: Optional[RegistryTool] = None,
        judge_tool: Optional[RegistryTool] = None,
        config: Optional[PipelineConfig] = None,
        prompts: Optional[PromptLibrary] = None,
        skills: Optional[SkillLibrary] = None,
    ):
        self.tool = control_tool or tool
        self.llm = llm
        self.reviewer = reviewer
        self.config = config or PipelineConfig()
        self.prompts = prompts or default_prompt_library()
        self.skills = skills or default_skill_library()
        self.inspector = InspectorPass(tool=inspector_tool or tool, skills=self.skills)
        self.planner = PlannerPass(
            tool=planner_tool or tool,
            llm=llm,
            prompts=self.prompts,
            max_tokens=self.config.planner_max_tokens,
        )
        self.implementer = ImplementerPass(
            tool=implementer_tool or tool,
            llm=llm,
            prompts=self.prompts,
            max_tokens=self.config.implementer_max_tokens,
        )
        self.judge = JudgePass(tool=judge_tool or tool, prompts=self.prompts)

    def run(self, packet: ContextPacket) -> PipelineResult:
        team_id = packet.subcontract.team_id
        task_id = packet.task.task_id

        self.tool.set_task_status(team_id, task_id, TaskStatus.ACTIVE)
        self.tool.emit_event(
            EventKind.SLICE_STARTED,
            team_id=team_id,
            payload={"task_id": task_id, "title": packet.task.title},
        )

        # 1. Inspector
        try:
            self.inspector.run(packet)
        except Exception as exc:  # pragma: no cover - defensive
            packet.notes.append(f"inspector errored: {exc!r}")
        self.tool.emit_event(
            EventKind.SLICE_INSPECTED,
            team_id=team_id,
            payload={"task_id": task_id, "n_capsules": len(packet.consumed_capsules)},
        )
        if packet.blockers and self.config.fail_fast_on_blocked:
            self.judge.run(packet, smoke_passed=None)
            self.tool.set_task_status(team_id, task_id, TaskStatus.BLOCKED)
            verdict = packet.verdict or SliceVerdict(
                approved=False,
                blockers=list(packet.blockers),
            )
            return PipelineResult(packet=packet, verdict=verdict, notes=list(packet.notes))

        # 2. Planner
        self.planner.run(packet)

        # 3. Implementer
        self.implementer.run(packet)
        self.tool.emit_event(
            EventKind.SLICE_IMPLEMENTED,
            team_id=team_id,
            payload={"task_id": task_id, "n_artifacts": len(packet.artifacts)},
        )

        # 4. Reviewer (independent LLM)
        try:
            self.reviewer(packet)
        except Exception as exc:  # pragma: no cover - defensive
            packet.notes.append(f"reviewer errored: {exc!r}")
            packet.reviewer_concerns.append("blocker: reviewer unavailable")
        self.tool.emit_event(
            EventKind.SLICE_REVIEWED,
            team_id=team_id,
            payload={"task_id": task_id, "n_concerns": len(packet.reviewer_concerns)},
        )

        # 4b. Smoke
        smoke_passed: Optional[bool] = None
        if self.config.smoke_runner is not None:
            try:
                smoke_passed = self.config.smoke_runner(packet)
            except Exception as exc:  # pragma: no cover
                packet.notes.append(f"smoke runner errored: {exc!r}")
                smoke_passed = False
        elif self.config.require_validation and packet.artifacts:
            packet.blockers.append("validation evidence missing")

        # 5. Judge
        self.judge.run(packet, smoke_passed=smoke_passed)

        verdict = packet.verdict or SliceVerdict(approved=False, blockers=["judge skipped"])
        return PipelineResult(packet=packet, verdict=verdict, notes=list(packet.notes))
