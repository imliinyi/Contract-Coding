from abc import ABC, abstractmethod
import ast
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from langgraph.graph import END

from MetaFlow.config import Config
from MetaFlow.flow.document_manager import DocumentManager
from MetaFlow.llm.client import LLM
from MetaFlow.prompt.system_prompt import CORE_SYSTEM_PROMPT
from MetaFlow.prompt.agent_prompt import AGENT_DETAILS, get_agent_prompt
from MetaFlow.utils.coding.python_executor import PyExecutor
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState
from MetaFlow.core.memory.memory_processor import MemoryProcessor



class BaseAgent(ABC):
    """
    Abstract BaseAgent class for the DAGAgent.
    It defines the common interface for all agents.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: Optional[List] = None, config: Config = None):
        self.agent_name = agent_name
        self.agent_prompt = agent_prompt
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.llm = LLM(
            api_key=self.config.OPENAI_API_KEY,
            api_base=self.config.OPENAI_API_BASE_URL,
            deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=self.config.OPENAI_API_MAX_TOKENS,
            temperature=self.config.OPENAI_API_TEMPERATURE
        )
        self.salaries: Dict[str, float] = self.config.AGENT_SALARIES

        self.system_prompt = self.get_system_prompt()
        self.custom_tools = custom_tools or []

        self.success = 0
        self.trails = 0
        self.success_rate = 0.0
        self.test_cases = None

    @staticmethod
    def validate_state(state: GeneralState | None) -> bool:
        if not state:
            self.logger.error("State is None")
            return False
        
        try:
            if not state.output:
                self.logger.error("State output is empty")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Error validating state: {e}")
            return False

    @staticmethod
    def get_system_prompt() -> str:
        """
        Get the system prompt for the agent.
        """
        return CORE_SYSTEM_PROMPT

    @staticmethod
    def get_agent_prompt() -> str:
        """
        Get the agent prompt for the agent.
        """
        return get_agent_prompt(self.agent_name)

    def get_prompt(self, task_description: str, prompt: str, 
            next_available_agents: List[str]) -> List[Dict[str, Union[str, List]]]:
        available_agents = ', '.join(f"{agent_name}: {AGENT_DETAILS[agent_name]}, " for agent_name in next_available_agents if agent_name in AGENT_DETAILS)
        system_prompt = self.system_prompt.format(
            available_agents=available_agents
        )
        prompt_template = """
        # User Overall Task
        {task_description}

        # Current Task
        {prompt}
        """

        return [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": self.agent_prompt},
            {"role": "user", "content": prompt_template.format(task_description=task_description, prompt=prompt)}
        ]

    @abstractmethod
    def _execute_agent(self, state: GeneralState, test_cases: List[str], 
        document_manager: DocumentManager, memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        Executes the agent's logic. This method MUST be implemented by all concrete subclasses.
        """
        raise NotImplementedError("This method should be implemented by a subclass.")

    def _parse_document_action(self, response_text: str, document_manager: DocumentManager):
        """
        Parses the <document_action> tag and executes the actions using the DocumentManager.
        """
        action_match = re.search(r'<document_action>(.*?)</document_action>', response_text, re.DOTALL)
        if action_match:
            action_json_str = action_match.group(1).strip()
            try:
                actions = json.loads(action_json_str)
                
                processed_actions = []
                for action in actions:
                    action_type = action.get('type')
                    
                    if action_type == 'add':
                        action['agent_name'] = self.agent_name
                    
                    processed_actions.append(action)

                if processed_actions:
                    document_manager.execute_actions(processed_actions)
            except (json.JSONDecodeError, TypeError) as e:
                self.logger.error(f"Failed to parse or execute document actions: {e}")

    def _parse_response(self, response_text: str, document_manager: DocumentManager, current_state: GeneralState) -> GeneralState:
        """
        Parses the raw response from the agent's execution and packages it into a new GeneralState object.
        """

        self._parse_document_action(response_text, document_manager)

        thinking_match = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        output_match = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)
        task_reqs_match = re.search(r'<task_requirements>(.*?)</task_requirements>', response_text, re.DOTALL)

        thinking = thinking_match.group(1).strip() if thinking_match else ""
        raw_output = output_match.group(1).strip() if output_match else response_text

        try:
            task_requirements = json.loads(task_reqs_match.group(1).strip()) if task_reqs_match else {END: raw_output}
        except (json.JSONDecodeError, AttributeError):
            task_requirements = {END: raw_output}
        
        next_agents = list(task_requirements.keys())
        next_agents = [agent for agent in next_agents if agent in self.salaries.keys()] or [END]

        # Create a new state, preserving the original task and sub_task from the input state
        new_state = current_state.model_copy(deep=True)
        new_state.role = self.agent_name
        new_state.thinking = thinking
        new_state.output = raw_output
        new_state.next_agents = next_agents
        new_state.task_requirements = task_requirements

        return new_state

    def update_success_rate(self) -> None:
        """
        Update the success rate of the agent.
        """
        self.success += 1
        self.success_rate = self.success / self.trails if self.trails > 0 else self.success_rate

    def extract_example(self, prompt: str) -> str:
        lines = (line.strip() for line in prompt.split('\n') if line.strip())

        results = []
        lines_iter = iter(lines)
        for line in lines_iter:
            if line.startswith('>>>'):
                function_call = line[4:]
                expected_output = next(lines_iter, None)
                if expected_output:
                    results.append(f"assert {function_call} == {expected_output}")

        self.test_cases = results

    def run_test(self, code: str) -> tuple[bool, str, GeneralState]:
        is_solved, feedback, state = PyExecutor().execute(code, self.test_cases, timeout=10)
        return is_solved, feedback, state


    