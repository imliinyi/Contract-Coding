from copy import deepcopy
import re
import logging

from collections import defaultdict
from typing import Any, Dict, Optional, List, Tuple
from langgraph.graph import END
from concurrent.futures import ThreadPoolExecutor, as_completed

from DAGAgent.DAG.memory import MemoryManager
from DAGAgent.DAG.desicion_space import DesicionSpace
from DAGAgent.agents.base_agent import BaseAgent
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
        self.termination_policy = self.config.TERMINATION_POLICY
        # self.max_workers = self.config.MAX_WORKERS
        self.is_train = True

        self.start_agent : Optional[str] = None
        self.agents : Dict[str, BaseAgent] = {END: BaseAgent(END)}

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

    def _run_single_agent(self, agent_name: str, state: GeneralState, 
            test_cases: List[str], next_available_agents: List[str]) -> GeneralState:
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

    def _run_single_step(self, input: str, test_cases: List[str] = []) -> Tuple[GeneralState, bool]:
        """
        Run a single step of the DAGAgent.
        """
        initial_state = self._initialize_state(input)
        
        # Data structures to manage the graph traversal
        # execution_trace will store the transition from one agent to the next with the immediate reward
        execution_trace: List[Tuple[str, str, float]] = []
        all_layers : List[Dict[str, GeneralState]] = [{self.start_agent: initial_state}]
        terminating_states : List[GeneralState] = []

        # executor = ThreadPoolExecutor(max_workers=self.max_workers)

        # Forward propagation
        while all_layers[-1]:
            current_level_agents = all_layers[-1]
            layer_index = len(all_layers) - 1

            layer_outputs = []
            # layer_futures = {}
            next_level_agents = defaultdict(list)

            for agent_name, state in current_level_agents.items():
                # state_copy = deepcopy(state)
                next_available_agents = self.decision_space.get_next_avail_agents(
                    agent_name=agent_name, 
                    next_available_agents=list(self.agents.keys()))
                output_state = self._run_single_agent(
                    agent_name=agent_name, 
                    state=state, 
                    test_cases=test_cases, 
                    next_available_agents=next_available_agents)
            #     future = executor.submit(
            #         self._run_single_agent, 
            #         agent_name, 
            #         state_copy, 
            #         test_cases, 
            #         next_available_agents
            #     )
            #     layer_futures[agent_name] = future

            # layer_results = []
            # for future in as_completed(layer_futures):
            #     agent_name = layer_futures[future]
            #     try:
            #         result= future.result()
            #         layer_results.append((agent_name, result))
            #     except Exception as e:
            #         logging.error(f"Error running agent {agent_name}: {e}")
            #         layer_results.append((agent_name, (state, True)))

                next_agents = output_state.next_agents
                continuing_agents, is_terminating = self._parse_agent_output(next_agents)
                
                layer_outputs.append({
                    'parent': agent_name,
                    'continuing_agents': continuing_agents,
                    'is_terminating': is_terminating,
                    'output_state': output_state,
                })

                for cont_n in continuing_agents:
                    next_level_agents[cont_n].append(output_state)

                num_terminating = sum([1 for o in layer_outputs if o['is_terminating']])
                num_total = len(layer_outputs)

                learn_terminating_only = False
                if self.termination_policy == 'any' and num_terminating > 0:
                    learn_terminating_only = True
                elif self.termination_policy == 'majority' and num_total > 0 and num_terminating > num_total / 2:
                    learn_terminating_only = True

                success_rate = [self.agents[agent].success_rate if agent in self.agents else 1 for agent in next_agents]
                group_reward = self.decision_space.calculate_group_reward(
                    current_state=agent_name, 
                    action_group=next_agents, 
                    path_len=layer_index, 
                    success_rates=success_rate,
                    learn_terminating_only=learn_terminating_only)

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

            if learn_terminating_only and self.termination_policy != 'all':
                    break

            next_level_agents = {
                agent_name: self.memory_manager.merge_memory(states)
                for agent_name, states in next_level_agents.items()
            }
            if not next_level_agents:
                break
            all_layers.append(next_level_agents)

        final_state = self.memory_manager.merge_memory(terminating_states) if terminating_states else None
        is_success = final_state is not None 

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

        # Update the success rate of the agents in the execution trace
        self._update_agents_success_rate(execution_trace)

        return final_state, is_success

    def _parse_agent_output(self, next_agents: Any) -> (List[str], bool):
        """
        Parse the output of an agent to determine the next agents to continue with.
        Returns a tuple of (next_agents, is_terminating).
        """
        if not next_agents or next_agents == END:
            return [], True
        if isinstance(next_agents, list):
            continuing_agents = [n for n in next_agents if n != END]
            is_terminating = len(continuing_agents) < len(next_agents)
            return continuing_agents, is_terminating
        # If the output is not a list, wrap it in a list
        return [next_agents], False

    def _update_agents_success_rate(self, execution_trace: List[Tuple[str, str, float]]) -> None:
        """
        Update the success rate of an agent.
        """
        # Update the success rate of the agents in the execution trace
        success_agents = set()
        reverse_graph = defaultdict(list)
        end_parents = set()
        for u, v, _ in execution_trace:
            reverse_graph[v].append(u)
            if v == END:
                end_parents.update(u)

        q = list(end_parents)
        visited = set(q)
        while(q):
            agent = q.pop(0)
            success_agents.add(agent)
            for parent in reverse_graph.get(agent, []):
                if parent not in visited:
                    q.append(parent)
                    visited.add(parent)

        for agent in success_agents:
            if agent in self.agents:
                self.agents[agent].update_success_rate()
        
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