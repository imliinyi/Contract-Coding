from abc import ABC, abstractmethod
import threading
from typing import Dict, List, Optional, Union

from ContractCoding.agents.prompt_builder import AgentPromptBuilder
from ContractCoding.agents.response_parser import AgentResponseParser
from ContractCoding.config import Config
from ContractCoding.llm.client import LLM
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


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
        self._llm_local = threading.local()

        self.system_prompt = self.get_system_prompt()
        self.custom_tools = custom_tools or []
        self.prompt_builder = AgentPromptBuilder(agent_name=agent_name, agent_prompt=agent_prompt, system_prompt=self.system_prompt)
        self.response_parser = AgentResponseParser(agent_name=agent_name, logger=self.logger)

    @property
    def llm(self) -> LLM:
        llm = getattr(self._llm_local, "llm", None)
        if llm is None:
            llm = LLM(
                deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
                api_key=self.config.OPENAI_API_KEY,
                api_base=self.config.OPENAI_API_BASE_URL,
                max_tokens=self.config.OPENAI_API_MAX_TOKENS,
                temperature=self.config.OPENAI_API_TEMPERATURE,
            )
            self._llm_local.llm = llm
        return llm

    @staticmethod
    def validate_state(state: GeneralState | None) -> bool:
        logger = get_logger()
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
        from ContractCoding.prompts.system_prompt import CORE_SYSTEM_PROMPT

        return CORE_SYSTEM_PROMPT

    def get_prompt(self, task_description: str, prompt: str, 
            next_available_agents: List[str]) -> List[Dict[str, Union[str, List]]]:
        return self.prompt_builder.build(
            task_description=task_description,
            current_task=prompt,
            next_available_agents=next_available_agents,
        )

    @abstractmethod
    def _execute_agent(self, state: GeneralState, document_manager: DocumentManager, memory_processor: MemoryProcessor, 
                next_available_agents: List[str]) -> GeneralState:
        """
        Executes the agent's logic. This method MUST be implemented by all concrete subclasses.
        """
        raise NotImplementedError("This method should be implemented by a subclass.")

    def _parse_tag_with_json(self, tag_name: str, text: str, expected_type: Union[type, None] = None) -> Optional[str]:
        return self.response_parser.parse_tag_with_json(tag_name, text, expected_type)

    def _parse_document_action(self, response_text: str, document_manager: DocumentManager):
        self.response_parser.apply_document_actions(response_text, document_manager)

    def _parse_response(self, response_text: str, document_manager: DocumentManager, current_state: GeneralState) -> GeneralState:
        return self.response_parser.parse_response(
            response_text=response_text,
            document_manager=document_manager,
            current_state=current_state,
        )

  
