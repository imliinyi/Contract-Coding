from typing import List, Dict, Any, Tuple, Optional
import json

from MetaFlow.agents.action_agent import ActionAgent
from MetaFlow.agents.llm_agent import LLMAgent
from MetaFlow.config import Config
from MetaFlow.tools.code_tool import run_code
from MetaFlow.tools.file_tool import list_directory, read_file, write_file
from MetaFlow.utils.state import GeneralState, Message


class ProjectManagerAgent(LLMAgent):
    """
    The Project Manager agent is responsible for analyzing initial requirements, 
    breaking them down into a high-level plan, and delegating tasks to other agents.
    """
    def __init__(self, config: Config):
        super().__init__("Project_Manager", config)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Tuple[Message, Optional[Dict[str, Any]]]:
        shared_context = state.shared_context
        # Determine the current status from the shared_context, default to PLANNING if not present.
        status = shared_context.get("status", "PLANNING") if shared_context else "PLANNING"

        # Dynamically build the prompt based on the current workflow status.
        if status == "PLANNING":
            prompt = f"""
            Task: {state.task}
            As the Project Manager, your current job is to initiate the project.
            1. Decompose the task into a high-level, multi-step plan following the universal workflow (e.g., a DESIGNING phase, an IMPLEMENTING phase, etc.).
            2. Create the initial `shared_context` with `status: \"DESIGNING\"` to start the design negotiation phase.
            3. In the `shared_context`, include the `plan` you just created.
            4. Delegate the initial design tasks to the appropriate expert agents based on the first step of your plan.
            """
        elif status == "DESIGNING":
            prompt = f"""
            The project is in the DESIGNING phase. You are the chief architect.
            1. Review the design proposals from all teams in the current `shared_context.design_document`.
            2. If there are conflicts, update the `shared_context` status to keep it as `DESIGNING`, describe the conflicts in `shared_context.conflicts`, and delegate revision tasks.
            3. If the designs are compatible, ratify the contract by setting the `shared_context.status` to `IMPLEMENTING` and delegate the implementation tasks.

            Current Shared Context:
            ```json
            {json.dumps(shared_context, indent=2)}
            ```
            """
        elif status == "IMPLEMENTING":
            prompt = f"""
            The project is in the IMPLEMENTING phase. The design has been ratified.
            Your job is to monitor the implementation progress. The last message indicates a sub-task is complete.
            Decide the next step. This might involve waiting for other parallel tasks to finish, or moving to the VALIDATING phase if all implementation is done.

            Current Shared Context:
            ```json
            {json.dumps(shared_context, indent=2)}
            ```
            Last message from other agent: {state.message.output}
            """
        elif status == "VALIDATING":
            prompt = f"""
            The project is in the VALIDATING phase. An expert has submitted their work for review.
            Your job is to delegate this work to a `CriticAgent` or a relevant QA agent to ensure it meets the requirements in the `design_document`.
            """
        elif status == "FINALIZING":
            prompt = f"""
            All project parts are complete and validated. Your final job is to integrate all parts into a single deliverable and terminate the process by calling the `END` agent.
            """
        else:
            prompt = f"The current status is '{status}'. Please decide the next appropriate action based on the overall goal."

        inputs = self.get_prompt(
            task_description=state.task,
            sys_prompt=self.get_system_prompt(),
            agent_prompt=self.get_agent_prompt(self.agent_name),
            prompt=prompt,
            next_available_agents=next_available_agents
        )

        response_text = self.llm.chat(inputs)
        message, updated_shared_context = self._parse_response(response_text)

        return message, updated_shared_context



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
