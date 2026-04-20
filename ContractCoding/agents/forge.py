from typing import Any, List

from ContractCoding.agents.agent import LLMAgent
from ContractCoding.config import Config
from ContractCoding.prompts.agents_prompt import get_agent_prompt
from ContractCoding.tools.code_tool import build_run_code
from ContractCoding.tools.file_tool import build_file_tools
from ContractCoding.tools.math_tool import solve_math_expression
from ContractCoding.tools.search_tool import search_web


class AgentCapability:
    def __init__(self, CODE: bool = False, MATH: bool = False, SEARCH: bool = False, FILE: bool = False):
        self.CODE = CODE
        self.MATH = MATH
        self.SEARCH = SEARCH
        self.FILE = FILE


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


class AgentForge:
    """
    AgentForge class for creating agents.
    """
    def __init__(self, config: Config):
        self.config = config

    def _forge_tools(self, capability: AgentCapability) -> List[Any]:
        tools = []
        if capability.CODE:
            tools.append(build_run_code(self.config.WORKSPACE_DIR))
        if capability.MATH:
            tools.append(solve_math_expression)
        if capability.SEARCH:
            tools.append(search_web)
        if capability.FILE:
            tools.extend(build_file_tools(self.config.WORKSPACE_DIR))

        return tools

    def create_agent(self, name: str, capability: AgentCapability) -> LLMAgent:
        """
        Create an agent with the given name.
        """
        agent_prompt = get_agent_prompt(name)

        tools = self._forge_tools(capability)

        if name in {"Project_Manager", "Architect"}:
            tools = []

        agent = LLMAgent(
            agent_name=name,
            agent_prompt=agent_prompt,
            custom_tools=tools,
            config=self.config
        )

        return agent

    def create_default_agents(self) -> dict[str, LLMAgent]:
        return {
            name: self.create_agent(name, capability)
            for name, capability in DEFAULT_AGENT_CAPABILITIES.items()
        }
