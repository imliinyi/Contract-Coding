from typing import List

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.llm.llm import LLM
from MetaFlow.config import Config
from MetaFlow.tools.code_tool import run_code
from MetaFlow.utils.state import Message, GeneralState


class ProjectManagerAgent(LLMAgent):
    """
    项目经理(Project Manager)智能体是复杂用户请求的入口点。
    它负责分析初始需求，将其分解为一个高层次的、可执行的计划，
    并将具体的子任务委派给合适的专业智能体。
    """
    def __init__(self, config: Config):
        # 将智能体的角色名 "Project_Manager" 传递给父类
        super().__init__("Project_Manager", config)


class ArchitectAgent(LLMAgent):
    """
    架构师(Architect)智能体负责设计软件的整体架构、数据库模式、
    API接口规范以及组件之间的交互方式。它产出供其他智能体遵循的技术蓝图。
    """
    def __init__(self, config: Config):
        # 将智能体的角色名 "Architect" 传递给父类
        super().__init__("Architect", config)


class QAEngineerAgent(ActionAgent):
    """
    The QA Engineer agent ensures product quality. 
    It is equipped with the `run_code` tool to write and execute unit tests, 
    integration tests, and more.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code]
        super().__init__("QA_Engineer", config, tools)


class UserProxyAgent(LLMAgent):
    """
    The User Proxy acts as the user's avatar within the system. When the system 
    encounters ambiguous requirements, it can proactively clarify issues or provide 
    feedback, reducing the frequency of interaction with the real user.
    """
    def __init__(self, config: Config):
        super().__init__("User_Proxy", config)
