from abc import ABC, abstractmethod
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from langgraph.graph import END

from MetaFlow.config import Config
from MetaFlow.llm.client import LLM
from MetaFlow.memory.document import DocumentManager
from MetaFlow.memory.processor import MemoryProcessor
from MetaFlow.prompts.agents_prompt import get_agent_prompt, AGENT_DETAILS
from MetaFlow.prompts.system_prompt import CORE_SYSTEM_PROMPT
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
            deployment_name=config.OPENAI_DEPLOYMENT_NAME,
            api_key=config.OPENAI_API_KEY,
            api_base=config.OPENAI_API_BASE_URL,
            max_tokens=config.OPENAI_API_MAX_TOKENS,
            temperature=config.OPENAI_API_TEMPERATURE
        )

        self.system_prompt = self.get_system_prompt()
        self.custom_tools = custom_tools or []


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
            return f"{agent_name}: {AGENT_DETAILS.get(agent_name, '')}"
        available_agents = ', '.join(_describe(agent_name) for agent_name in next_available_agents)
        system_prompt = self.system_prompt

        if self.agent_name == "Project_Manager":
            system_prompt = system_prompt + f"""
                # Available Agents: {available_agents}  
            """
        
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
    def _execute_agent(self, state: GeneralState, document_manager: DocumentManager, memory_processor: MemoryProcessor, 
                next_available_agents: List[str]) -> GeneralState:
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
        # We don't rely on task_requirements for scheduling anymore, but we keep it optional for logging if agents still output it.
        # But we remove the strict validation.
        
        return GeneralState(
            task=current_state.task,
            sub_task=current_state.sub_task,
            role=self.agent_name,
            thinking=thinking,
            output=raw_output,
            next_agents=[], # No longer determined by LLM directly
            task_requirements={}, # Optional
        )

  