from ast import Tuple
import re
from collections import defaultdict

from typing import Dict, Optional, List
from langgraph.graph import END


from DAGAgent.DAG.memory import MemoryManager
from DAGAgent.DAG.desicion_space import DesicionSpace
from DAGAgent.agents.base_agent import BaseAgent
from DAGAgent.llm.llm import LLM
from DAGAgent.config import Config
from DAGAgent.utils.state import Message, GeneralState
from DAGAgent.utils.coding.python_executor import execute_code_get_return
from DAGAgent.utils.math.get_predict import get_predict


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
        )

    def _init_decision_space(self) -> None:
        """
        Initialize the decision space.
        """
        agents = list(self.agents.keys())
        self.decision_space = DesicionSpace(agents, self.config)

    def _run_single_agent(self, agent_name: str, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> GeneralState:
        """
        Run a single agent.
        """
        agent = self.agents[agent_name]
        message = agent._execute_agent(state, test_cases, next_available_agents)

        # Extract code from the message output
        code_pattern = r'```python\n(.*?)```'
        code_match = re.search(code_pattern, message.output, re.DOTALL)
        code = code_match.group(1).strip() if code_match else ''

        if code:
            answer = execute_code_get_return(code)
        else:
            answer = get_predict(message.output)

        next_agents = agent.get_next_agents(message)

        current_state = GeneralState(
            task=state.task,
            code=code if code else state.code,
            answer=answer,
            message=message,
            next_agents=next_agents,
        )

        return current_state

    def _run_single_step(self, input: str, test_cases: List[str] = []) -> GeneralState:
        """
        Run a single step of the DAGAgent.
        """
        initial_state = self._initialize_state(input)
        # Data structures to manage the graph traversal
        # edge_rewards will store the immediate reward for each transition
        # edge_rewards = []
        # execution_graph = []
        execution_trace: List[Tuple[str, str, float]] = []
        all_layers : List[Dict[str, GeneralState]] = [{self.start_agent: initial_state}]
        terminating_states : List[GeneralState] = []

        # Forward propagation
        while all_layers[-1]:
            current_level_agents = all_layers[-1]
            layer_index = len(all_layers) - 1
            next_level_agents = defaultdict(list)

            for agent_name, state in current_level_agents.items():
                next_available_agents = self.decision_space.get_next_avail_agents(
                    agent_name=agent_name, 
                    next_available_agents=list(self.agents.keys()))
                output_state = self._run_single_agent(
                    agent_name=agent_name, 
                    state=state, 
                    test_cases=test_cases, 
                    next_available_agents=next_available_agents)

                next_agents = output_state.next_agents
                if not next_agents: next_agents = END
                if not isinstance(next_agents, list): next_agents = [next_agents]

                success_rate = [self.agents[agent].success_rate if agent in self.agents else 1 for agent in next_agents]
                group_reward = self.decision_space.calculate_group_reward(
                    current_state=agent_name, 
                    action_group=next_agents, 
                    path_len=layer_index, 
                    success_rates=success_rate)

                # execution_graph.append((agent_name, next_agents))
                # edge_reward = []
                for next_agent in next_agents:
                    # success_rate = self.agents[agent_name].success_rate if agent_name in self.agents else 1
                    # reward = self.decision_space.calculate_reward(
                    #     agent_name=agent_name, 
                    #     next_agent=next_agent, 
                    #     layer_index=layer_index, 
                    #     success_rate=success_rate)
                    # edge_reward.append(reward)
                    reward = group_reward.get(next_agent, 0)
                    execution_trace.append((agent_name, next_agent, reward))

                    if next_agent != END:
                        next_level_agents[next_agent].append(output_state)
                    else:
                        terminating_states.append(output_state)
                
                # edge_rewards.append(edge_reward)

            next_level_agents = {
                agent_name: self.memory_manager.merge_memory(states)
                for agent_name, states in next_level_agents.items()
            }
            if not next_level_agents:
                break
            all_layers.append(next_level_agents)

        # Backward propagation
        state_values = defaultdict(float)
        forward_graph = defaultdict(list)
        for u, v, _ in execution_trace:
            if v not in forward_graph:
                forward_graph[v].append(u)

        for layer in reversed(all_layers):
            for agent_name in layer:
                value = 0
                if agent_name in forward_graph:
                    # Find all conversions that actually occurred from this node during this run
                    outgoing_edges = [trace for trace in execution_trace if trace[0] == agent_name]
                    for _, next_agent, reward in outgoing_edges:
                        value += reward + state_values[next_agent]
                state_values[agent_name] = value
        
        # Convert the execution trace to experiences
        experiences = []
        for u, v, r in execution_trace:
            experiences.append({
                "state": u,
                "action": v,
                "reward": r,
            })

        if self.is_train:
            self.decision_space.update_from_experience(experiences)
            self.decision_space.decay_epsilon()

        self.decision_space.save_q_table()



        return final_state
        
    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name

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