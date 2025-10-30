from typing import Any, Dict, List, Optional, Tuple

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.config import Config
from MetaFlow.flow.state_processor import StateProcessor
from MetaFlow.flow.document_manager import DocumentManager
from MetaFlow.utils.state import GeneralState
from MetaFlow.utils.log import get_logger

class AgentRunner: # This now acts as our AgentExecutor
    def __init__(
        self,
        config: Config,
        agents: Dict[str, BaseAgent],
        state_processor: StateProcessor,
        document_manager: DocumentManager,
    ):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents = agents
        self.state_processor = state_processor
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
            state_processor=self.state_processor,
        )

        return output_state
