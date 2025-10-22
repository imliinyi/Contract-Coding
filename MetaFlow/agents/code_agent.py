from typing import List, Dict, Any, Tuple, Optional
import json

from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.config import Config
from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.file_tool import list_directory, read_file, write_file
from MetaFlow.utils.state import GeneralState, Message


class FrontendEngineerAgent(ActionAgent):
    """
    The Frontend Engineer agent is responsible for building the user interface and interaction logic of the application.
    It uses HTML, CSS, and JavaScript to create responsive and interactive web pages.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Frontend_Engineer", config, tools)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Tuple[Message, Optional[Dict[str, Any]]]:
        shared_context = state.shared_context
        status = shared_context.get("status", "") if shared_context else ""

        design_prompt = f"""
            Your sub-task is: {state.sub_task}
            As the Frontend Engineer, you are in the DESIGNING phase. Propose the UI components, their states, and the API data you need from the backend.
            Add your proposal to a new `frontend` section within the `design_document` of the `shared_context`.
            You MUST output a `<shared_context>` block. Then, return control to the Project_Manager.
            """
        implementation_prompt = f"""
            Your sub-task is: {state.sub_task}
            The project is in the IMPLEMENTING phase. The design has been ratified.
            Your task is to implement the frontend based on the final design in the `shared_context.design_document`.
            Use your tools to write the necessary HTML, CSS, and JavaScript files.

            Approved Design:
            ```json
            {json.dumps(shared_context.get("design_document"), indent=2)}
            ```
            """

        # inputs = self.get_prompt(
        #     task_description=state.task,
        #     sys_prompt=self.get_system_prompt(),
        #     agent_prompt=self.get_agent_prompt(self.agent_name),
        #     prompt=prompt,
        #     next_available_agents=next_available_agents
        # )

        # response_text = self.llm.chat_with_tools(messages=inputs, tools=self.tools)
        # message, updated_shared_context = self._parse_response(response_text)

        return self._run_expert_logic(
            state=state,
            stage_prompt=design_prompt if status == "DESIGNING" else implementation_prompt,
            next_available_agents=next_available_agents
        )


class BackendEngineerAgent(ActionAgent):
    """
    The Backend Engineer agent is responsible for handling the application's business logic and data storage.
    It uses server-side languages like Python, Java, or Node.js to build the application's server-side components. 
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Backend_Engineer", config, tools)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Tuple[Message, Optional[Dict[str, Any]]]:
        shared_context = state.shared_context
        status = shared_context.get("status", "") if shared_context else ""

        design_prompt = f"""    
            Your sub-task is: {state.sub_task}
            As the Backend Engineer, you are in the DESIGNING phase. Propose the API endpoints, request/response schemas, and database models.
            Add your proposal to a new `backend` section within the `design_document` of the `shared_context`.
            You MUST output a `<shared_context>` block. Then, return control to the Project_Manager.
            """
        implementation_prompt = f"""
            Your sub-task is: {state.sub_task}
            The project is in the IMPLEMENTING phase. The design has been ratified.
            Your task is to implement the backend API based on the final design in the `shared_context.design_document`.
            Use your tools to write the server code and define the database.

            Approved Design:
            ```json
            {json.dumps(shared_context.get("design_document"), indent=2)}
            ```
            """


        return self._run_expert_logic(
            state=state,
            stage_prompt=design_prompt if status == "DESIGNING" else implementation_prompt,
            next_available_agents=next_available_agents
        )


        # inputs = self.get_prompt(
        #     task_description=state.task,
        #     sys_prompt=self.get_system_prompt(),
        #     agent_prompt=self.get_agent_prompt(self.agent_name),
        #     prompt=prompt,
        #     next_available_agents=next_available_agents
        # )

        # response_text = self.llm.chat_with_tools(messages=inputs, tools=self.tools)
        # message, updated_shared_context = self._parse_response(response_text)

        # return message, updated_shared_context


class AlgorithmEngineerAgent(ActionAgent):
    """
    The Algorithm Engineer agent is responsible for designing and implementing complex algorithms and models.
    It uses mathematical, statistical, and programming skills to solve real-world problems and optimize system performance.
    """
    def __init__(self, config: Config):
        # Define the list of tools for this agent
        tools = [run_code, read_file, write_file, list_directory]
        super().__init__("Algorithm_Engineer", config, tools)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Tuple[Message, Optional[Dict[str, Any]]]:
        shared_context = state.shared_context
        status = shared_context.get("status", "") if shared_context else ""

        design_prompt = f"""
            Your sub-task is: {state.sub_task}
            As the Algorithm Engineer, you are in the DESIGNING phase. Propose the algorithm's pseudocode, complexity analysis, and data I/O format.
            Add your proposal to a new `algorithm` section within the `design_document` of the `shared_context`.
            You MUST output a `<shared_context>` block. Then, return control to the Project_Manager.
            """
        implementation_prompt = f"""
            Your sub-task is: {state.sub_task}
            The project is in the IMPLEMENTING phase. The design has been ratified.
            Your task is to implement the algorithm based on the final design in the `shared_context.design_document`.
            Use your tools to write and test the algorithm code.

            Approved Design:
            ```json
            {json.dumps(shared_context.get("design_document"), indent=2)}
            ```
            """
        # else:
        #      prompt = f"Your sub-task is: {state.sub_task}. The project status is '{status}'. Please proceed using your available tools as appropriate."

        return self._run_expert_logic(
            state=state,
            stage_prompt=design_prompt if status == "DESIGNING" else implementation_prompt,
            next_available_agents=next_available_agents
        )

        # inputs = self.get_prompt(
        #     task_description=state.task,
        #     sys_prompt=self.get_system_prompt(),
        #     agent_prompt=self.get_agent_prompt(self.agent_name),
        #     prompt=prompt,
        #     next_available_agents=next_available_agents
        # )

        # response_text = self.llm.chat_with_tools(messages=inputs, tools=self.tools)
        # message, updated_shared_context = self._parse_response(response_text)

        # return message, updated_shared_context


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


# class CodeDesignerAgent(LLMAgent):
#     """
#     The Code Designer agent is responsible for creating high-level code designs, 
#     including architecture diagrams, component flowcharts, and code templates.
#     """
#     def __init__(self, config: Config):
#         super().__init__("Code_Designer", config)
