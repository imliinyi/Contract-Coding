from typing import Dict, Optional

from DAGAgent.utils.state import Message
from DAGAgent.llm.llm import LLM
from DAGAgent.config import Config
from DAGAgent.DAG.memory import MemoryManager
from DAGAgent.agents.base_agent import BaseAgent
from DAGAgent.utils.state import GeneralState
from DAGAgent.DAG.desicion_space import DesicionSpace

from langgraph.graph import END


class DynaFlow:
    """
    DynaFlow class for the DAGAgent.
    """
    def __init__(self, config: Config, memory_window: int):
        self.config = config
        self.memory_window = memory_window
        self.memory_manager = MemoryManager(self.config, self.memory_window)
        self.decision_space: Optional[DesicionSpace] = None

        self.start_agent : Optional[str] = None
        self.agents : Dict[str, BaseAgent] = {END: BaseAgent(END)}

    def _initialize_state(self, input: str) -> GeneralState:
        """
        Initialize the state for the input.
        """
        return GeneralState(
            task=input,
            code="",
            answer="",
            message=Message(role="user", thinking="", output=""),
            next_agents=self.start_agent,
            executed_agents={}
        )

    def _run_single_step(self, input: str, test_cases: List[str] = []) -> GeneralState:
        """
        Run a single step of the DAGAgent.
        """
        initial_state = self._initialize_state(input)
        # Data structures to manage the graph traversal
        # edge_rewards will store the immediate reward for each transition
        edge_rewards = {}
        # execution_graph is the `executed_nodes` from the state
        execution_graph = initial_state.executed_nodes


        return initial_state
        
    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent(agent_name)

    def init_decision_space(self) -> None:
        """
        Initialize the decision space.
        """
        agents = list(self.agents.keys())
        self.decision_space = DesicionSpace(agents, self.config)

    
        