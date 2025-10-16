from typing import List
from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.utils.state import GeneralState, Message
from MetaFlow.config import Config
from MetaFlow.prompt.system_prompt import CORE_SYSTEM_PROMPT

from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.file_tool import read_file, write_file, list_directory


class SoftwareEngineerAgent(ActionAgent):
    """
    软件工程师(Software Engineer)智能体是编码的主力。
    它配备了文件读写、目录查看和代码执行的全套工具，能够根据
    架构设计和任务要求，独立完成代码的编写、调试、修改和测试。
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Software_Engineer", config, tools)


class CodeReviewerAgent(LLMAgent):
    """
    The Code Reviewer agent analyzes code for quality, style, and adherence to best practices.
    It acts as an automated peer reviewer, suggesting improvements to readability, 
    performance, and maintainability without executing the code.
    """
    def __init__(self, config: Config):
        super().__init__("Code_Reviewer", config)


class DatabaseAdminAgent(ActionAgent):
    """
    The Database Admin agent is a professional data steward, specializing in all 
    deep operations directly related to the database. It can write complex SQL queries,
    perform database migrations, and handle performance tuning.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code]
        super().__init__("Database_Admin", config, tools)


class SecurityAnalystAgent(ActionAgent):
    """
    The Security Analyst is the system's guardian, responsible for proactively 
    identifying and fixing potential security risks during the development process.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code, read_file]
        super().__init__("Security_Analyst", config, tools)


class DevOpsEngineerAgent(ActionAgent):
    """
    The DevOps Engineer is responsible for deployment and operations. It focuses on 
    deploying completed applications to servers and ensuring their stable operation.
    """
    def __init__(self, config: Config):
        super().__init__("DevOps_Engineer", config)
        # Equip the agent with tools to write config files and run shell scripts
        tools = [run_code, write_file, list_directory]
        super().__init__("DevOps_Engineer", config, tools)
