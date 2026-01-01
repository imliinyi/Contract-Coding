from typing import Any, List

from MetaFlow.agents.agent import LLMAgent
from MetaFlow.config import Config
from MetaFlow.prompts.agents_prompt import get_agent_prompt
from MetaFlow.tools.code_tool import build_run_code
from MetaFlow.tools.file_tool import build_file_tools
from MetaFlow.tools.math_tool import solve_math_expression
from MetaFlow.tools.search_tool import search_web


class AgentCapability:
    def __init__(self, CODE: bool = False, MATH: bool = False, SEARCH: bool = False, FILE: bool = False):
        self.CODE = CODE
        self.MATH = MATH
        self.SEARCH = SEARCH
        self.FILE = FILE


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
            # tools.append(search_web)
            pass
        if capability.FILE:
            tools.extend(build_file_tools(self.config.WORKSPACE_DIR))

        return tools

    def create_agent(self, name: str, capability: AgentCapability) -> LLMAgent:
        """
        Create an agent with the given name.
        """
        agent_prompt = get_agent_prompt(name)

        tools = self._forge_tools(capability)

        if name == "Project_Manager":
            tools = [t for t in tools if getattr(t, "__name__", "") != "write_file"]

        agent = LLMAgent(
            agent_name=name,
            agent_prompt=agent_prompt,
            custom_tools=tools,
            config=self.config
        )

        return agent
