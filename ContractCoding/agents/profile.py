"""Agent profile and capability registry for long-running runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class AgentCapability:
    CODE: bool = False
    MATH: bool = False
    SEARCH: bool = False
    FILE: bool = False


@dataclass(frozen=True)
class AgentProfile:
    name: str
    role_kind: str
    prompt_key: str
    capabilities: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    allowed_work_kinds: List[str] = field(default_factory=list)
    risk_policy: str = "normal"

    def accepts(self, work_kind: str) -> bool:
        return "*" in self.allowed_work_kinds or work_kind in self.allowed_work_kinds


DEFAULT_AGENT_PROFILES: Dict[str, AgentProfile] = {
    "Project_Manager": AgentProfile(
        name="Project_Manager",
        role_kind="Goal Strategist",
        prompt_key="Project_Manager",
        capabilities=["goal_intake", "contract_authoring"],
        allowed_tools=["document"],
        allowed_work_kinds=["*"],
        risk_policy="plan-only",
    ),
    "Architect": AgentProfile(
        name="Architect",
        role_kind="Contract Compiler",
        prompt_key="Architect",
        capabilities=["contract_validation", "graph_compilation"],
        allowed_tools=["document", "search"],
        allowed_work_kinds=["*"],
        risk_policy="plan-only",
    ),
    "Backend_Engineer": AgentProfile(
        name="Backend_Engineer",
        role_kind="Implementation Worker",
        prompt_key="Backend_Engineer",
        capabilities=["coding", "runtime", "backend"],
        allowed_tools=["file", "code", "process"],
        allowed_work_kinds=["coding", "ops", "data"],
        risk_policy="implementation",
    ),
    "Frontend_Engineer": AgentProfile(
        name="Frontend_Engineer",
        role_kind="Implementation Worker",
        prompt_key="Frontend_Engineer",
        capabilities=["coding", "ui", "frontend"],
        allowed_tools=["file", "code", "browser"],
        allowed_work_kinds=["coding", "doc"],
        risk_policy="implementation",
    ),
    "Algorithm_Engineer": AgentProfile(
        name="Algorithm_Engineer",
        role_kind="Implementation Worker",
        prompt_key="Algorithm_Engineer",
        capabilities=["coding", "algorithm", "data"],
        allowed_tools=["file", "code", "math"],
        allowed_work_kinds=["coding", "data"],
        risk_policy="implementation",
    ),
    "Test_Engineer": AgentProfile(
        name="Test_Engineer",
        role_kind="Test Worker",
        prompt_key="Test_Engineer",
        capabilities=["testing", "coding", "verification"],
        allowed_tools=["file", "code", "process"],
        allowed_work_kinds=["coding", "eval"],
        risk_policy="test-generation",
    ),
    "Critic": AgentProfile(
        name="Critic",
        role_kind="Verifier",
        prompt_key="Critic",
        capabilities=["verification", "spec_review"],
        allowed_tools=["file", "code", "search"],
        allowed_work_kinds=["coding", "research", "doc", "ops", "data", "eval"],
        risk_policy="review",
    ),
    "TeamReviewer": AgentProfile(
        name="TeamReviewer",
        role_kind="Team Gate Reviewer",
        prompt_key="Critic",
        capabilities=["team_review", "spec_review"],
        allowed_tools=["file", "code", "search"],
        allowed_work_kinds=["coding", "research", "doc", "ops", "data", "eval"],
        risk_policy="gate-review",
    ),
    "FinalReviewer": AgentProfile(
        name="FinalReviewer",
        role_kind="Final Gate Reviewer",
        prompt_key="Code_Reviewer",
        capabilities=["integration_review", "product_review"],
        allowed_tools=["file", "code", "process"],
        allowed_work_kinds=["coding", "research", "doc", "ops", "data", "eval"],
        risk_policy="gate-review",
    ),
    "Code_Reviewer": AgentProfile(
        name="Code_Reviewer",
        role_kind="Integrator",
        prompt_key="Code_Reviewer",
        capabilities=["integration", "runtime_review"],
        allowed_tools=["file", "code", "process"],
        allowed_work_kinds=["coding", "ops"],
        risk_policy="review",
    ),
    "Researcher": AgentProfile(
        name="Researcher",
        role_kind="Research Worker",
        prompt_key="Researcher",
        capabilities=["research", "synthesis"],
        allowed_tools=["file", "search"],
        allowed_work_kinds=["research", "doc"],
        risk_policy="read-mostly",
    ),
    "Technical_Writer": AgentProfile(
        name="Technical_Writer",
        role_kind="Documentation Worker",
        prompt_key="Technical_Writer",
        capabilities=["documentation", "synthesis"],
        allowed_tools=["file"],
        allowed_work_kinds=["doc", "research"],
        risk_policy="content-write",
    ),
    "Run_Steward": AgentProfile(
        name="Run_Steward",
        role_kind="Run Steward",
        prompt_key="Project_Manager",
        capabilities=["resume", "dedupe", "blocked_state_summary"],
        allowed_tools=["document"],
        allowed_work_kinds=["*"],
        risk_policy="control-plane",
    ),
    "Recovery_Orchestrator": AgentProfile(
        name="Recovery_Orchestrator",
        role_kind="Final Recovery Orchestrator",
        prompt_key="Recovery_Orchestrator",
        capabilities=["failure_classification", "repair_routing", "final_gate_recovery"],
        allowed_tools=["file", "code"],
        allowed_work_kinds=["eval", "coding"],
        risk_policy="control-plane",
    ),
    "Evaluator": AgentProfile(
        name="Evaluator",
        role_kind="Evaluation Worker",
        prompt_key="Evaluator",
        capabilities=["eval", "benchmarking", "failure_analysis"],
        allowed_tools=["file", "code", "process"],
        allowed_work_kinds=["eval", "coding", "research", "doc", "data"],
        risk_policy="evaluation",
    ),
}


DEFAULT_AGENT_CAPABILITIES = {
    "Project_Manager": AgentCapability(FILE=True),
    "Critic": AgentCapability(FILE=True, CODE=True, MATH=True, SEARCH=True),
    "Code_Reviewer": AgentCapability(FILE=True, CODE=True),
    "TeamReviewer": AgentCapability(FILE=True, CODE=True, SEARCH=True),
    "FinalReviewer": AgentCapability(FILE=True, CODE=True, SEARCH=True),
    "Technical_Writer": AgentCapability(FILE=True, CODE=True, MATH=True, SEARCH=True),
    "Editing": AgentCapability(FILE=True),
    "Researcher": AgentCapability(FILE=True, SEARCH=True),
    "Mathematician": AgentCapability(FILE=True, MATH=True),
    "Proof_Assistant": AgentCapability(FILE=True, MATH=True),
    "Data_Scientist": AgentCapability(FILE=True, MATH=True, SEARCH=True),
    "Frontend_Engineer": AgentCapability(FILE=True, CODE=True),
    "Backend_Engineer": AgentCapability(FILE=True, CODE=True),
    "Algorithm_Engineer": AgentCapability(FILE=True, CODE=True),
    "Test_Engineer": AgentCapability(FILE=True, CODE=True),
    "Evaluator": AgentCapability(FILE=True, CODE=True, MATH=True, SEARCH=True),
    "Architect": AgentCapability(FILE=True, CODE=True),
    "Recovery_Orchestrator": AgentCapability(FILE=True, CODE=True),
}


class AgentProfileRegistry:
    def __init__(self, profiles: Optional[Iterable[AgentProfile]] = None):
        self._profiles: Dict[str, AgentProfile] = dict(DEFAULT_AGENT_PROFILES)
        if profiles:
            for profile in profiles:
                self.register(profile)

    def register(self, profile: AgentProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str) -> Optional[AgentProfile]:
        if name in self._profiles:
            return self._profiles[name]
        normalized = self._normalize(name)
        for profile in self._profiles.values():
            candidates = [profile.name, profile.role_kind]
            if normalized in {self._normalize(candidate) for candidate in candidates}:
                return profile
        return None

    def resolve_agent_name(self, requested: str, work_kind: str = "coding") -> str:
        profile = self.get(requested)
        if profile and profile.accepts(work_kind):
            return profile.name

        for fallback_name in self._fallback_order(work_kind):
            fallback = self._profiles.get(fallback_name)
            if fallback and fallback.accepts(work_kind):
                return fallback.name
        return requested or "Project_Manager"

    def all(self) -> List[AgentProfile]:
        return list(self._profiles.values())

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    @staticmethod
    def _fallback_order(work_kind: str) -> List[str]:
        if work_kind == "doc":
            return ["Technical_Writer", "Researcher", "Project_Manager"]
        if work_kind == "research":
            return ["Researcher", "Technical_Writer", "Project_Manager"]
        if work_kind == "ops":
            return ["Backend_Engineer", "Code_Reviewer", "Project_Manager"]
        if work_kind == "data":
            return ["Algorithm_Engineer", "Backend_Engineer", "Project_Manager"]
        if work_kind == "eval":
            return ["Evaluator", "Critic", "Project_Manager"]
        return ["Backend_Engineer", "Frontend_Engineer", "Algorithm_Engineer", "Project_Manager"]
