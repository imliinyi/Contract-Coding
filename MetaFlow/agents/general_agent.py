import copy
import json
from typing import Any, Dict, List, Optional, Tuple

from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.config import Config
from MetaFlow.flow.decision_space import logger
from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.file_tool import list_directory, read_file, write_file
from MetaFlow.utils.state import GeneralState, Message
from MetaFlow.flow.document_manager import DocumentManager


class ProjectManagerAgent(LLMAgent):
    """
    The Project Manager agent is responsible for analyzing initial requirements, 
    breaking them down into a high-level plan, and delegating tasks to other agents.
    """
    def __init__(self, config: Config):
        super().__init__("Project_Manager", config)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str],
        document_manager: DocumentManager) -> Message:
        prompt = state.sub_task if state.sub_task else state.task

        inputs = self.get_prompt(
            task_description=state.task,
            sys_prompt=self.get_system_prompt(),
            agent_prompt=self.get_agent_prompt(self.agent_name),
            prompt=prompt,
            next_available_agents=next_available_agents
        )

        response_text = self.llm.chat(inputs)
        logger.info(f"==========ProjectManagerAgent {self.agent_name} output: {response_text}")

        message = self._parse_response(response_text, document_manager)


        return message



class CriticAgent(ActionAgent):
    """
    The Critic agent evaluates the progress, quality, and cost of the project. 
    It provides feedback and suggestions to help the Project Manager adjust the plan.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [read_file, write_file, list_directory]
        super().__init__("Critic", config, tools)


# class ArchitectAgent(LLMAgent):
#     """
#     架构师(Architect)智能体负责设计软件的整体架构、数据库模式、
#     API接口规范以及组件之间的交互方式。它产出供其他智能体遵循的技术蓝图。
#     """
#     def __init__(self, config: Config):
#         # 将智能体的角色名 "Architect" 传递给父类
#         super().__init__("Architect", config)


# class QAEngineerAgent(ActionAgent):
#     """
#     The QA Engineer agent ensures product quality. 
#     It is equipped with the `run_code` tool to write and execute unit tests, 
#     integration tests, and more.
#     """
#     def __init__(self, config: Config):
#         # Define the list of tools for this agent
#         tools = [run_code]
#         super().__init__("QA_Engineer", config, tools)


# class UserProxyAgent(LLMAgent):
#     """
#     The User Proxy acts as the user's avatar within the system. When the system 
#     encounters ambiguous requirements, it can proactively clarify issues or provide 
#     feedback, reducing the frequency of interaction with the real user.
#     """
#     def __init__(self, config: Config):
#         super().__init__("User_Proxy", config)
