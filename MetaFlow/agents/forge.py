from enum import Enum
from typing import List

from MetaFlow.agents.agent import LLMAgent, GUIAgent
from MetaFlow.config import Config
from MetaFlow.prompt.agent_prompt import get_agent_prompt
from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.file_tool import file_tree, list_directory, read_lines, write_file, update_file_lines, read_file
from MetaFlow.tools.math_tool import solve_math_expression
from MetaFlow.tools.search_tool import search_web
from MetaFlow.tools.process_tool import start_process



class AgentCapability:
    def __init__(self, CODE: bool = False, MATH: bool = False, SEARCH: bool = False, FILE: bool = False, GUI: bool = False):
        self.CODE = CODE
        self.MATH = MATH
        self.SEARCH = SEARCH
        self.FILE = FILE
        self.GUI = GUI


class AgentForge:
    """
    AgentForge class for creating agents.
    """
    def __init__(self, config: Config):
        self.config = config

    def _forge_tools(self, capability: AgentCapability) -> List[str]:
        tools = []
        if capability.CODE:
            tools.append([run_code, start_process])
        if capability.MATH:
            tools.append(solve_math_expression)
        if capability.SEARCH:
            tools.append(search_web)
        if capability.FILE:
            tools.extend([file_tree, read_lines, write_file, list_directory, read_file])

        return tools

    def create_agent(self, name: str, capability: AgentCapability) -> LLMAgent:
        """
        Create an agent with the given name.
        """
        agent_prompt = get_agent_prompt(name)

        tools = self._forge_tools(capability)
        if capability.GUI:
            agent = GUIAgent(
                agent_name=name,
                agent_prompt=agent_prompt,
                config=self.config
            )
        else:
            agent = LLMAgent(
                agent_name=name,
                agent_prompt=agent_prompt,
                custom_tools=tools,
                config=self.config
            )
        return agent
