import re
import logging
from collections import defaultdict
from typing import Any, Dict, Optional, List, Tuple

from langgraph.graph import END

from MetaFlow.flow.memory import MemoryManager
from MetaFlow.flow.desicion_space import DesicionSpace
from MetaFlow.flow.composite_graph import CompositeGraph, CompositeAgent
from MetaFlow.flow.graph_traverser import GraphTraverser
from MetaFlow.flow.agent_runner import AgentRunner
from MetaFlow.flow.learner import Learner
from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.config import Config
from MetaFlow.reflection.reflector import Reflector
from MetaFlow.reflection.triggers import check_layer_revisit, check_long_path
from MetaFlow.utils.state import Message, GeneralState

logger = logging.getLogger(__name__)


class MetaFlow:

    def __init__(self, config: Config):
        self.config = config
        self.agents : Dict[str, BaseAgent] = {END: BaseAgent(END, config)}
        self.start_agent : Optional[str] = None
        self.is_train = True
        self.termination_policy = self.config.TERMINATION_POLICY

        self.memory_manager = MemoryManager(self.config, self.config.MEMORY_WINDOW)
        self.decision_space: Optional[DesicionSpace] = None
        self.agent_runner: Optional[AgentRunner] = AgentRunner(self.agents)
        self.graph_traverser: Optional[GraphTraverser] = None
        self.learner: Optional[Learner] = None
        self.reflector = Reflector(self.config)

        if self.termination_policy not in ['any', 'majority', 'all']:
            raise ValueError("TERMINATION_PLOICY must be one of ['any', 'majority', 'all'].")

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
        )

    def _init_decision_space(self) -> None:
        """
        Initialize the decision space.
        """
        agents = list(self.agents.keys())
        self.decision_space = DesicionSpace(agents, self.config)
        self.graph_traverser = GraphTraverser(
            config=self.config,
            agents=self.agents,
            decision_space=self.decision_space,
            agent_runner=self.agent_runner,
            memory_manager=self.memory_manager
        )
        self.learner = Learner(self.config, self.decision_space, self.agents)

    def _run_single_step(self, input: str, test_cases: List[str] = []) -> Tuple[GeneralState, bool]:
        """
        Run a single step of the DAGAgent.
        """
        initial_state = self._initialize_state(input)
        
        # Forward propagation
        all_layers, execution_trace, terminating_states = self.graph_traverser.traverse(
            self.start_agent, initial_state, test_cases)

        final_state = terminating_states[0] if terminating_states else None
        is_success = final_state is not None

        # Reflect and learn
        if is_success:
            self.reflect_and_learn(execution_trace, all_layers)

        # Backward propagation
        if self.is_train and execution_trace:
            self.learner.learn(all_layers, execution_trace)

        return final_state, is_success

    def _convert_to_graph(self, edge_list: List[List[str]]) -> Dict[str, List[str]]:
        """
        Convert the edge list to a graph.
        """
        graph = defaultdict(list)
        for u, v in edge_list:
            graph[u].append(v)
        return dict(graph)

    def reflect_and_learn(self, trace_graph: List[Tuple[str, str]], executed_layers: List[frozenset]) -> None:
        """
        Reflect and learn from the trace graph.
        """
        is_reflect = check_layer_revisit(executed_layers) or \
                     check_long_path(executed_layers, self.config.PATH_THRESHOLD)

        if not is_reflect:
            return # No need to reflect

        # Convert trace_graph to edge list format
        trace_structure = [(u, v) for u, v, _ in trace_graph]
        new_skill = self.reflector.abstract_skill(trace_structure)
        if not new_skill or not new_skill.get('skill_name'):
            return  # No new skill to learn

        skill_name = new_skill['skill_name']
        if skill_name in self.agents:
            return  # Skill already exists

        composite_agent = CompositeAgent(
            agent_name=skill_name,
            config=self.config,
            sub_graph=self._convert_to_graph(new_skill['sub_graph']),
            agents=self.agents,
        )
        self.register_agent(skill_name, composite_agent)

        # Add the new skill to the decision space
        entry_point = new_skill['sub_graph'][0][0]
        self.decision_space.add_new_action(entry_point, skill_name, entry_point)
        
    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name

    def train(self, inputs: List[str], test_cases: List[List[str]]) -> List[Dict[str, Any]]:
        """
        Train the DAGAgent on the given inputs and test cases.
        """
        self.is_train = True
        assert len(inputs) == len(test_cases), "Number of inputs must match number of test cases."

        results = []
        logger.info(f"--- Training on {len(inputs)} samples ---")
        for i, (input_task, tests) in enumerate(zip(inputs, test_cases)):
            final_state, is_success = self._run_single_step(input_task, tests)
            results.append({
                'input_task': input_task,
                'test_cases': tests,
                'final_state': final_state,
                'answer': final_state.answer,
                'is_success': is_success,
            })
            logger.info(f"--- Sample {i+1}/{len(inputs)} - Final Answer: {final_state.answer if final_state else 'N/A'} - Success: {is_success} ---")

        logger.info(f"--- Training Finished ---")
        return results

    def run(self, input_task: str, test_cases: List[str]) -> str:
        """
        Run the DAGAgent on the given input task and test cases.
        """
        self.is_train = False
        final_state, is_success = self._run_single_step(input_task, test_cases)
        return final_state.answer

    def find_all_paths(self, graph: Dict[str, List[str]], start: str, end: str) -> List[List[str]]:
        """
        Find all paths from start_agent to END in the graph.
        """
        def dfs(current: str, path: List[str], paths: List[List[str]]):
            path.append(current)
            if current == end:
                paths.append(path.copy())
            elif current in graph:
                for neighbor in graph.get(current, []):
                    dfs(neighbor, path, paths)
            path.pop()

        all_paths = []
        dfs(start, [], all_paths)
        return all_paths