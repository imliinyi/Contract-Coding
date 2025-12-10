from abc import ABC, abstractmethod
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from langgraph.graph import END

from MetaFlow.config import Config
from MetaFlow.core.memory.document_manager import DocumentManager
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.llm.client import LLM
from MetaFlow.prompt.agent_prompt import AGENT_DETAILS, get_agent_prompt
from MetaFlow.prompt.system_prompt import CORE_SYSTEM_PROMPT
from MetaFlow.utils.exception import EmptyTaskRequirementsError
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState


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
            api_key=config.OPENAI_API_KEY,
            api_base=config.OPENAI_API_BASE_URL,
            deployment_name=config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=config.OPENAI_API_MAX_TOKENS,
            temperature=config.OPENAI_API_TEMPERATURE
        )
        self.salary = config.AGENT_SALARY
        # self.salaries: Dict[str, float] = config.AGENT_SALARIES

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

    def get_agent_prompt(self) -> str:
        """
        Get the agent prompt for the agent.
        """
        return get_agent_prompt(self.agent_name)

    def get_prompt(self, task_description: str, prompt: str, 
            next_available_agents: List[str]) -> List[Dict[str, Union[str, List]]]:
        # Include dynamic skills even if not in AGENT_DETAILS, with a generic description
        def _describe(agent_name: str) -> str:
            return f"{agent_name}: {AGENT_DETAILS.get(agent_name, 'Dynamic Skill (Composite Subgraph)')}"
        available_agents = ', '.join(_describe(agent_name) for agent_name in next_available_agents)
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
            {"role": "assistant", "content": f"# Your Role Guideline:\n {self.agent_prompt}"},
            {"role": "user", "content": prompt_template.format(task_description=task_description, prompt=prompt)}
        ]

    @abstractmethod
    def _execute_agent(self, state: GeneralState, test_cases: List[str], document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        Executes the agent's logic. This method MUST be implemented by all concrete subclasses.
        """
        raise NotImplementedError("This method should be implemented by a subclass.")

    def _parse_tag_with_json(self, tag_name: str, text: str, expected_type: Union[type, None] = None) -> Optional[str]:
        """
        A robust parser to extract JSON content from a specific tag, handling various formats.
        It first finds the tag, then extracts the full JSON object/list from within.
        """
        # A simple regex to find the content between the tags, ignoring markdown noise
        pattern = re.compile(rf"<{tag_name}>(.*?)</{tag_name}>", re.DOTALL)
        match = pattern.search(text)

        if not match:
            # Fallback for markdown header format
            pattern_md = re.compile(rf"###\s*<{tag_name}>\s*\n```json(.*?)\n```", re.DOTALL)
            match = pattern_md.search(text)

        if match:
            content_str = match.group(1).strip()
            
            # Determine the start and end characters based on expected type or content
            start_char, end_char = None, None
            if expected_type is list or content_str.startswith('['):
                start_char, end_char = '[', ']'
            elif expected_type is dict or content_str.startswith('{'):
                start_char, end_char = '{', '}'

            if start_char:
                start_pos = content_str.find(start_char)
                last_pos = content_str.rfind(end_char)
                if start_pos != -1 and last_pos > start_pos:
                    return content_str[start_pos : last_pos + 1]

            # If no specific JSON structure is found, return the raw content for simple cases
            return content_str
            
        return None

    def _parse_document_action(self, response_text: str, document_manager: DocumentManager):
        """
        Parses the <document_action> tag and executes the actions using the DocumentManager.
        """
        action_json_str = self._parse_tag_with_json("document_action", response_text, expected_type=list)
        if action_json_str:
            try:
                actions = json.loads(action_json_str)
                
                processed_actions = []
                for action in actions:
                    action_type = action.get('type')
                    
                    if action_type in ('add', 'update'):
                        action['agent_name'] = self.agent_name
                        action['base_version'] = document_manager.get_version()
                        # Keep only update semantics; merge handled by layer aggregator based on base_version
                    
                    processed_actions.append(action)

                if processed_actions:
                    try:
                        if hasattr(document_manager, 'is_aggregating') and document_manager.is_aggregating():
                            document_manager.queue_actions(processed_actions)
                        else:
                            document_manager.execute_actions(processed_actions)
                    except Exception as e:
                        self.logger.error(f"Document action handling failed: {e}")
            except (json.JSONDecodeError, TypeError) as e:
                self.logger.error(f"Failed to parse or execute document actions: {e}")

    def _parse_response(self, response_text: str, document_manager: DocumentManager, current_state: GeneralState) -> GeneralState:
        """
        Parses the raw response from the agent's execution and packages it into a new GeneralState object.
        """

        self._parse_document_action(response_text, document_manager)

        thinking_match = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        output_match = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)
        
        thinking = thinking_match.group(1).strip() if thinking_match else ""
        raw_output = output_match.group(1).strip() if output_match else response_text

        task_requirements = None
        task_reqs_json_str = self._parse_tag_with_json("task_requirements", response_text, expected_type=dict)
        if task_reqs_json_str:
            try:
                task_requirements = json.loads(task_reqs_json_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON format in <task_requirements> tag: {e}")
        
        if not task_requirements:
            raise EmptyTaskRequirementsError("The <task_requirements> tag is missing, empty, or invalid.")
        
        next_agents = list(task_requirements.keys())
        # next_agents = [agent for agent in next_agents if agent in self.salaries.keys()] or [END]

        return GeneralState(
            task=current_state.task,
            sub_task=current_state.sub_task,
            role=self.agent_name,
            thinking=thinking,
            output=raw_output,
            next_agents=next_agents,
            task_requirements=task_requirements,
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

    # def run_test(self, code: str) -> tuple[bool, str, GeneralState]:
    #     is_solved, feedback, state = PyExecutor().execute(code, self.test_cases, timeout=10)
    #     return is_solved, feedback, state


    
