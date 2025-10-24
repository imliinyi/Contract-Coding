from abc import ABC, abstractmethod
import ast
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from langgraph.graph import END

from MetaFlow.config import Config
from MetaFlow.flow.document_manager import DocumentManager
from MetaFlow.llm.llm import LLM
from MetaFlow.prompt.agent_prompt import AGENT_DETAILS, get_agent_prompt
from MetaFlow.prompt.system_prompt import CORE_SYSTEM_PROMPT
from MetaFlow.utils.coding.python_executor import PyExecutor
from MetaFlow.utils.state import GeneralState, Message


logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    """
    Abstract BaseAgent class for the DAGAgent.
    It defines the common interface for all agents.
    """
    def __init__(self, agent_name: str, config: Config):
        self.agent_name = agent_name
        self.config = config
        self.llm = LLM(
            api_key=self.config.OPENAI_API_KEY,
            api_base=self.config.OPENAI_API_BASE_URL,
            deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=self.config.OPENAI_API_MAX_TOKENS,
            temperature=self.config.OPENAI_API_TEMPERATURE
        )
        self.salaries: Dict[str, float] = self.config.AGENT_SALARIES

        self.success = 0
        self.trails = 0
        self.success_rate = 0.0
        self.test_cases = None

    @staticmethod
    def validate_state(state: Message | None) -> bool:
        if not state:
            logger.error("State is None")
            return False
        
        try:
            if not state.output:
                logger.error("State output is empty")
                return False
            return True
        except Exception as e:
            logger.error(f"Error validating state: {e}")
            return False

    @staticmethod
    def get_system_prompt() -> str:
        """
        Get the system prompt for the agent.
        """
        return CORE_SYSTEM_PROMPT

    @staticmethod
    def get_agent_prompt(agent_name: str) -> str:
        """
        Get the agent prompt for the agent.
        """
        return get_agent_prompt(agent_name)

    @staticmethod
    def get_prompt(task_description: str, sys_prompt: str, agent_prompt: str, prompt: str, 
            next_available_agents: List[str]) -> List[Dict[str, Union[str, List]]]:
        available_agents = ', '.join(f"{agent_name}: {AGENT_DETAILS[agent_name]}, " for agent_name in next_available_agents if agent_name in AGENT_DETAILS)
        system_prompt = sys_prompt.format(
            task_description=task_description,
            agent_prompt=agent_prompt,
            available_agents=available_agents
        )
        return [
            {"role": "system", "content": [{"type": "text", "text": system_prompt},]},
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        ]

    @abstractmethod
    def _execute_agent(self, state: GeneralState, test_cases: List[str], 
        document_manger: DocumentManager, next_available_agents: List[str]) -> Message:
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
                document_manager.execute_actions(actions)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to parse or execute document actions: {e}")

    def _parse_response(self, response_text: str, document_manager: DocumentManager) -> Message:
        """
        Parses the raw response from the agent's execution and packages it into a Message object.
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
        
        if isinstance(task_requirements, dict):
            for key, value in task_requirements.items():
                if not isinstance(value, str):
                    task_requirements[key] = json.dumps(value, ensure_ascii=False)
        
        next_agents = list(task_requirements.keys())

        return Message(
            role=self.agent_name,
            thinking=thinking,
            output=raw_output,
            next_agents=next_agents,
            task_requirements=task_requirements
        )


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

    def run_test(self, code: str) -> tuple[bool, str, Message]:
        is_solved, feedback, state = PyExecutor().execute(code, self.test_cases, timeout=10)
        return is_solved, feedback, state


    