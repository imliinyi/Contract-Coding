from typing import Dict

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.constants import END
from ContractCoding.orchestration.harness import TaskHarness
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


class AgentRunner:
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
        self.harness = TaskHarness(config=config, document_manager=document_manager)

    def run(
        self,
        agent_name: str,
        state: GeneralState,
        next_available_agents: list,
    ) -> GeneralState:
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning(f"Agent {agent_name} not found, skipping.")
            state.output = f"Error: Agent {agent_name} not found."
            state.next_agents = [END]
            return state

        result = self.harness.execute(
            agent=agent,
            agent_name=agent_name,
            state=state,
            next_available_agents=next_available_agents,
            memory_processor=self.memory_processor,
        )

        return result.output_state


AgentExecutor = AgentRunner
