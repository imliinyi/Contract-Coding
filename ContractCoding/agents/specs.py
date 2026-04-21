from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCapability:
    CODE: bool = False
    MATH: bool = False
    SEARCH: bool = False
    FILE: bool = False


DEFAULT_AGENT_CAPABILITIES = {
    "Project_Manager": AgentCapability(FILE=True),
    "Critic": AgentCapability(FILE=True, CODE=True, MATH=True, SEARCH=True),
    "Code_Reviewer": AgentCapability(FILE=True, CODE=True),
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
    "Architect": AgentCapability(FILE=True, CODE=True),
}
