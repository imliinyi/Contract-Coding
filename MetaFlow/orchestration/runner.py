from typing import Any, Dict, List, Optional, Tuple

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.core.memory.document_manager import DocumentManager   
from MetaFlow.utils.state import GeneralState
from MetaFlow.utils.log import get_logger

class AgentRunner: # This now acts as our AgentExecutor
    def __init__(
        self,
        config: Config,
        agents: Dict[str, BaseAgent],
        memory_processor: MemoryProcessor,
        document_manager: DocumentManager,
    ):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents = agents
        self.memory_processor = memory_processor
        self.document_manager = document_manager

    def run(
        self, 
        agent_name: str, 
        state: GeneralState, 
        test_cases: list, 
        next_available_agents: list
    ) -> GeneralState:
        """Executes a single agent and returns the resulting state."""
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning(f"Agent {agent_name} not found, skipping.")
            state.output = f"Error: Agent {agent_name} not found."
            state.next_agents = ["END"]
            return state

        # The agent execution is now a single, clean call.
        # The agent itself handles memory, LLM calls, and parsing.
        output_state = agent._execute_agent(
            state=state,
            test_cases=test_cases,
            next_available_agents=next_available_agents,
            document_manager=self.document_manager,
            memory_processor=self.memory_processor,
        )

        return output_state
