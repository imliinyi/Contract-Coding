"""5-pass worker pipeline (v2).

For each scheduled TeamWorkItem, the worker runs these passes in sequence:

  1. Inspector  — pull the minimal context from the registry:
                    * consumed capsules (L1 tag always, L2 only if the task
                      explicitly depends on them)
                    * team working paper + open decisions
                    * prior failures matching the task fingerprint
  2. Planner    — turn a work item goal into concrete subtasks + boundaries.
  3. Implementer — produce concrete artifacts (code, tests, docs) inside the
                    team workspace.
  4. Reviewer    — run the INDEPENDENT reviewer LLM (different client). See
                    `reviewer.LLMReviewer`.
  5. Judge       — aggregate acceptance signals (reviewer verdict + smoke +
                    invariant checks) into a final verdict.

All cross-pass state flows via a `ContextPacket` dataclass. Every pass emits
a progress entry + appropriate events. Cross-team coordination happens through
typed contract operations, not natural-language worker messages.

LLM access is abstracted behind `LLMPort`. Plugging in the legacy
`NullLLMPort` is provided so the pipeline is testable offline.
"""

from __future__ import annotations

from .protocol import LLMPort, LLMRequest, LLMResult, NullLLMPort
from .packet import ContextPacket, SlicePlan, SliceArtifact, SliceVerdict
from .pipeline import WorkerPipeline, PipelineConfig, PipelineResult
from .passes import PlannerPass, InspectorPass, ImplementerPass, JudgePass

__all__ = [
    "LLMPort",
    "LLMRequest",
    "LLMResult",
    "NullLLMPort",
    "ContextPacket",
    "SlicePlan",
    "SliceArtifact",
    "SliceVerdict",
    "WorkerPipeline",
    "PipelineConfig",
    "PipelineResult",
    "PlannerPass",
    "InspectorPass",
    "ImplementerPass",
    "JudgePass",
]
