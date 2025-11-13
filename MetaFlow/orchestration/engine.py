from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from langgraph.graph import END

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.core.decision_space.decision_space import DecisionSpace
from MetaFlow.core.graph.composer import CompositeAgent
from MetaFlow.core.graph.traverser import GraphTraverser
from MetaFlow.core.memory.document_manager import DocumentManager
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.core.reflection.reflector import Reflector
from MetaFlow.core.reflection.triggers import check_layer_revisit, check_long_path
from MetaFlow.orchestration.learner import Learner
from MetaFlow.orchestration.runner import AgentRunner
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState


class Engine:

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents : Dict[str, BaseAgent | CompositeAgent | None] = {END: None}
        self.start_agent : Optional[str] = None
        self.is_train = True
        self.termination_policy = config.TERMINATION_POLICY

        self.memory_processor = MemoryProcessor(self.config, list(self.agents.keys()), self.config.MEMORY_WINDOW)
        self.decision_space: Optional[DecisionSpace] = None
        self.graph_traverser: Optional[GraphTraverser] = None
        self.learner: Optional[Learner] = None
        self.reflector = Reflector(self.config)
        self.document_manager = DocumentManager()

        if self.termination_policy not in ['any', 'majority', 'all']:
            raise ValueError("TERMINATION_PLOICY must be one of ['any', 'majority', 'all'].")

    def _initialize_state(self, input: str) -> GeneralState:
        """
        Initialize the state for the input.
        """
        return GeneralState(
            task=input,
            sub_task="",
            role="user",
            thinking="",
            output="",
            next_agents=[self.start_agent],
            task_requirements={self.start_agent: input}
        )

    def _init_decision_space(self) -> None:
        """
        Initialize the decision space.
        """
        agents = list(self.agents.keys())
        self.decision_space = DecisionSpace(agents, self.config)

        self.agent_runner = AgentRunner(
            config=self.config,
            agents=self.agents,
            memory_processor=self.memory_processor,
            document_manager=self.document_manager
        )

        self.graph_traverser = GraphTraverser(
            config=self.config,
            agent_runner=self.agent_runner,
            decision_space=self.decision_space,
            memory_processor=self.memory_processor, 
            document_manager=self.document_manager
        )

        self.learner = Learner(self.config, self.decision_space, self.agents)

    def _check_cycle(self, new_skill_name: str, sub_graph: List[Tuple[str, str]]) -> bool:
        """
        Check if the new skill name will cause a cycle in the graph.
        """
        dependencies = defaultdict(list)
        for agent_name, agent in self.agents.items():
            if isinstance(agent, CompositeAgent):
                # Add dependencies from composite agent to its sub-agents
                for _, target_agent in agent.sub_graph:
                    if target_agent != END:
                        dependencies[agent_name].append(target_agent)

        for _, target_agent in sub_graph:
            if target_agent != END:
                dependencies[new_skill_name].append(target_agent)

        # DFS to detect cycle
        visiting = set()
        visited = set()

        def has_cycle(node):
            visiting.add(node)
            for neighbor in dependencies.get(node, []):
                if neighbor in visiting:
                    return True  # Cycle detected
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True  # Cycle detected
            visiting.remove(node)
            visited.add(node)
            return False  # No cycle detected

        return has_cycle(new_skill_name)

    def _run_single_step(self, input: str, test_cases: List[str] = []) -> Tuple[GeneralState, bool]:
        """
        Run a single step of the MetaFlow.
        """
        self.document_manager = DocumentManager() # Reset for each run
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

    def reflect_and_learn(self, execution_trace: List[Tuple[str, str, float]], all_layers: List[Dict[str, GeneralState]]) -> None:
        """
        Reflect and learn from the execution trace.
        """
        is_reflect = check_layer_revisit(all_layers) or \
                     check_long_path(all_layers, self.config.PATH_THRESHOLD)

        if not is_reflect:
            return # No need to reflect

        # The reflector now takes the full execution history to build a rich representation
        new_skill = self.reflector.abstract_skill(all_layers, execution_trace)
        if not new_skill or not new_skill.get('skill_name'):
            return  # No new skill to learn

        skill_name = new_skill['skill_name']
        if skill_name in self.agents:
            return  # Skill already exists

        # Check if the new skill will cause a cycle
        if self._check_cycle(skill_name, new_skill['sub_graph']):
            self.logger.warning(f"Cycle detected when adding skill {skill_name}. Skipping learning.")
            return  # Cycle detected, skip learning

        composite_agent = CompositeAgent(
            agent_name=skill_name,
            config=self.config,
            decision_space=self.decision_space,
            sub_graph=new_skill['sub_graph'],
            agents=self.agents,
            document_manager=self.document_manager
        )
        self.register_agent(skill_name, composite_agent)

        # Add the new skill to the decision space
        state = new_skill['sub_graph'][0][1]
        if '_1' in state:
            state = state.replace('_1', '')
        # entry_point = new_skill['skill_name']
        self.decision_space.add_new_action(state, skill_name)
        
    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name

    def train(self, inputs: List[str], test_cases: List[List[str]]) -> List[Dict[str, Any]]:
        """
        Train the MetaFlow on the given inputs and test cases.
        """
        self.is_train = True
        assert len(inputs) == len(test_cases), "Number of inputs must match number of test cases."
        if self.graph_traverser is None:
            self._init_decision_space()

        results = []
        self.logger.info(f"--- Training on {len(inputs)} samples ---")
        for i, (input_task, tests) in enumerate(zip(inputs, test_cases)):
            final_state, is_success = self._run_single_step(input_task, tests)
            # os.environ['WORKSPACE_ID'] = str(i + 1)
            time.sleep(10) # Avoid too many requests to the server
            results.append({
                'input_task': input_task,
                'test_cases': tests,
                'final_state': final_state,
                'is_success': is_success,
            })
            self.logger.info(f"--- Sample {i+1}/{len(inputs)} - Success: {is_success} ---")

        self.logger.info(f"--- Training Finished ---")
        return results

    def run(self, input_task: str, test_cases: List[str]) -> str:
        """
        Run the MetaFlow on the given input task and test cases.
        """
        self.is_train = False
        if self.graph_traverser is None:
            self._init_decision_space()
        final_state, is_success = self._run_single_step(input_task, test_cases)
        return final_state

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