from typing import Dict

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.constants import END
from ContractCoding.execution.work_harness import WorkHarness, TaskResult
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


class AgentRunner:
    def __init__(
        self,
        config: Config,
        agents: Dict[str, BaseAgent],
        context_manager: ContextManager,
    ):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents = agents
        self.context_manager = context_manager
        self.harness = WorkHarness(config=config)

    def run(
        self,
        agent_name: str,
        state: GeneralState,
        next_available_agents: list,
    ) -> TaskResult:
        agent = self.agents.get(agent_name)
        if not agent:
            self.logger.warning(f"Agent {agent_name} not found, skipping.")
            state.output = f"Error: Agent {agent_name} not found."
            state.next_agents = [END]
            return TaskResult(output_state=state, changed_files=set(), validation_errors=[state.output])

        return self.harness.execute(
            agent=agent,
            agent_name=agent_name,
            state=state,
            next_available_agents=next_available_agents,
            context_manager=self.context_manager,
        )


AgentExecutor = AgentRunner
