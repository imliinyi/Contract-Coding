from typing import Any, Dict, List, Optional, Tuple

from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.config import Config
from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.file_tool import file_tree, list_directory, read_file, write_file
from MetaFlow.utils.state import GeneralState, Message


class FrontendEngineerAgent(ActionAgent):
    """
    The Frontend Engineer agent is responsible for building the user interface and interaction logic of the application.
    It uses HTML, CSS, and JavaScript to create responsive and interactive web pages.
    """
    def __init__(self, config: Config):
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Frontend_Engineer", config, tools)


class BackendEngineerAgent(ActionAgent):
    """
    The Backend Engineer agent is responsible for handling the application's business logic and data storage.
    It uses server-side languages like Python, Java, or Node.js to build the application's server-side components. 
    """
    def __init__(self, config: Config):
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Backend_Engineer", config, tools)


class AlgorithmEngineerAgent(ActionAgent):
    """
    The Algorithm Engineer agent is responsible for designing and implementing complex algorithms and models.
    It uses mathematical, statistical, and programming skills to solve real-world problems and optimize system performance.
    """
    def __init__(self, config: Config):
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Algorithm_Engineer", config, tools)


class CodeReviewerAgent(ActionAgent):
    """
    The Code Reviewer agent analyzes code for quality, style, and adherence to best practices.
    It acts as an automated peer reviewer, suggesting improvements to readability, 
    performance, and maintainability without executing the code.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Code_Reviewer", config, tools)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Message:
        file_tree_str = file_tree('.')
        state.sub_task = state.sub_task + f"\nCurrent File Tree:\n{file_tree_str}"
        return super()._execute_agent(state, test_cases, next_available_agents)


# class CodeDesignerAgent(LLMAgent):
#     """
#     The Code Designer agent is responsible for creating high-level code designs, 
#     including architecture diagrams, component flowcharts, and code templates.
#     """
#     def __init__(self, config: Config):
#         super().__init__("Code_Designer", config)
