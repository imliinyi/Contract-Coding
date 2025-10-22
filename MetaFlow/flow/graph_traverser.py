from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from langgraph.graph import END

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.config import Config
from MetaFlow.flow.agent_runner import AgentRunner
from MetaFlow.flow.decision_space import DecisionSpace
from MetaFlow.flow.memory import MemoryManager
from MetaFlow.utils.state import GeneralState, Message


class GraphTraverser:
    def __init__(
        self,
        config: Config,
        agents: Dict[str, BaseAgent],
        decision_space: DecisionSpace,
        agent_runner: AgentRunner,
        memory_manager: MemoryManager,
    ):
        self.config = config
        self.agents = agents
        self.decision_space = decision_space
        self.agent_runner = agent_runner
        self.memory_manager = memory_manager
        self.termination_policy = self.config.TERMINATION_POLICY

    def sub_traverse(
        self,
        sub_graph: List[Dict[str, str]],
        initial_states: GeneralState,
        test_cases: List[str],
    ) -> Tuple[List[Dict[str, GeneralState]], List[Tuple[str, str, float]], List[GeneralState]]:
        """
        Forward propagation through the layers of the graph.
        """
        execution_trace: List[Dict[str, str, float]] = []
        all_layers: List[Dict[str, GeneralState]] = []
        terminating_states: List[GeneralState] = []

        # Build the forward graph
        forward_graph = defaultdict(list)
        for u, v in sub_graph:
            forward_graph[u].append(v)

        entry_points = forward_graph.get('START', [])
        if not entry_points:
            return [], [], []

        current_layer_states = {agent_name: initial_states for agent_name in set(entry_points)}
        executed_layers = 0

        while current_layer_states and executed_layers < self.config.MAX_LAYERS:
            all_layers.append(current_layer_states)
            executed_layers += 1
            next_layer_inputs = defaultdict(list)

            for agent_name, input_state in current_layer_states.items():
                output_state = self.agent_runner.run(
                    agent_name=agent_name, 
                    state=input_state, 
                    test_cases=test_cases, 
                    next_available_agents=[])
                successors = forward_graph.get(agent_name, [])
                task_reqs = output_state['message'].get('task_requirements', {})
                for successor in successors:
                    # Record the execution trace
                    execution_trace.append((agent_name, successor, 0.0))

                    if successor == END:
                        terminating_states.append(output_state)
                    else:
                        # Collect the output state for the successor agent
                        sub_task = task_reqs.get(successor, '')
                        new_state = output_state.copy()
                        new_state['sub_task'] = sub_task
                        next_layer_inputs[successor].append(new_state)

            next_layer_states = {}
            for agent_name, states in next_layer_inputs.items():
                if len(states) > 1:
                    merged_state = self.memory_manager.merge_memory(states)
                    next_layer_states[agent_name] = merged_state
                else:
                    next_layer_states[agent_name] = states[0]

            current_layer_states = next_layer_states

        return all_layers, execution_trace, terminating_states

    def traverse(
        self, start_agent: str, initial_states: Dict[str, GeneralState], test_cases: List[str]
    ) -> Tuple[List[Dict[str, GeneralState]], List[Tuple[str, str, float]], List[GeneralState]]:
        """
        Forward propagation through the layers of the graph.
        """
        execution_trace: List[Dict[str, str, float]] = []
        all_layers: List[Dict[str, GeneralState]] = [{start_agent: initial_states}]
        terminating_states: List[GeneralState] = []

        while all_layers[-1] and len(all_layers) <= self.config.MAX_LAYERS:
            current_level_agents = all_layers[-1]
            layer_index = len(all_layers) - 1

            layer_outputs = []
            # layer_futures = {}
            next_level_agents = defaultdict(list)
            remaining_agents = list(current_level_agents.keys())

            for agent_name, state in current_level_agents.items():
                print(f"\n--- Current Agent: {agent_name} ---")
                remaining_agents.remove(agent_name)
                print(f"--- Remaining Agents in Current Layer: {remaining_agents} ---")

                # state_copy = deepcopy(state)
                # next_available_agents = self.decision_space.get_next_avail_agents(
                #     state=agent_name, 
                #     available_agents=list(self.agents.keys()))
                # The current agent cannot delegate to itself. Remove it from the list of available agents.
                next_available_agents = [name for name in self.agents.keys() if name != agent_name]
                output_message, code, answer, shared_context = self.agent_runner.run(
                    agent_name=agent_name, 
                    state=state, 
                    test_cases=test_cases, 
                    next_available_agents=next_available_agents)

                # Add the output state to memory
                self.memory_manager.add_message(agent_name, output_message)

                next_agents = output_message.next_agents
                continuing_agents, is_terminating = self._parse_agent_output(next_agents)
                output_state = GeneralState(
                    task=state.task,
                    sub_task=state.sub_task,
                    shared_context=shared_context, 
                    code=code,
                    answer=answer,
                    message=output_message)
                
                layer_outputs.append({
                    'agent_name': agent_name,
                    'next_agents': next_agents,
                    'continuing_agents': continuing_agents,
                    'is_terminating': is_terminating,
                    'output_state': output_state,
                })

                # for cont_n in continuing_agents:
                #     next_level_agents[cont_n].append(output_state)

            num_terminating = sum([1 for o in layer_outputs if o['is_terminating']])
            num_total = len(layer_outputs)

            learn_terminating_only = False
            if self.termination_policy == 'any' and num_terminating > 0:
                learn_terminating_only = True
            elif self.termination_policy == 'majority' and num_total > 0 and num_terminating > num_total / 2:
                learn_terminating_only = True

            for output in layer_outputs:
                agent_name = output['agent_name']
                next_agents = output['next_agents'] or []
                continuing_agents = output['continuing_agents']
                output_state = output['output_state']

                success_rate = [self.agents[agent].success_rate if agent in self.agents and self.agents[agent] is not None else 1 for agent in next_agents]
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

                    # if next_agent != END:
                    #     next_level_agents[next_agent].append(output_state)
                    # else:
                    #     terminating_states.append(output_state)
                    if next_agent == END:
                        terminating_states.append(output_state)
                
                # edge_rewards.append(edge_reward)
                for cont_n in continuing_agents:
                    # Create a new state for each downstream agent with its specific sub-task
                    task_reqs = output_state.message.task_requirements
                    sub_task = task_reqs.get(cont_n, output_state.sub_task)
                    new_state_for_next_agent = output_state.copy()
                    new_state_for_next_agent.sub_task = sub_task
                    next_level_agents[cont_n].append(new_state_for_next_agent)

            if learn_terminating_only and self.termination_policy != 'all':
                break

            next_level_agents = {
                agent_name: self.memory_manager.merge_memory(states)
                for agent_name, states in next_level_agents.items()
            }
            if not next_level_agents:
                print("--- Next Layer Agents: [] ---")
                break
            print(f"--- Next Layer Agents: {list(next_level_agents.keys())} ---")
            all_layers.append(next_level_agents)

        return all_layers, execution_trace, terminating_states

    def _parse_agent_output(self, next_agents: Any) -> Tuple[List[str], bool]:
        """
        Parse the output of an agent to determine the next agents to continue with.
        Returns a tuple of (next_agents, is_terminating).
        """
        if not next_agents or next_agents == END:
            return [], True
        if isinstance(next_agents, list):
            # Remove END from the list of next agents
            continuing_agents = [n for n in next_agents if n != END]
            is_terminating = len(continuing_agents) < len(next_agents)
            return continuing_agents, is_terminating
        # If the output is not a list, wrap it in a list
        return [next_agents], False
