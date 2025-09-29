from calendar import c
from collections import defaultdict
from typing import List, Tuple, Dict, Any, Optional

from annotated_types import Ge
from langgraph.graph import END

from MetaFlow.config import Config
from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.flow.agent_runner import AgentRunner
from MetaFlow.flow.memory import MemoryManager
from MetaFlow.flow.desicion_space import DesicionSpace
from MetaFlow.utils.state import GeneralState


class GraphTraverser:
    def __init__(
        self,
        config: Config,
        agents: Dict[str, BaseAgent],
        decision_space: DesicionSpace,
        agent_runner: AgentRunner,
        memory_manager: MemoryManager,
    ):
        self.config = config
        self.agents = agents
        self.decision_space = decision_space
        self.agent_runner = agent_runner
        self.memory_manager = memory_manager

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

            for agent_name, state in current_level_agents.items():
                # state_copy = deepcopy(state)
                next_available_agents = self.decision_space.get_next_avail_agents(
                    agent_name=agent_name, 
                    next_available_agents=list(self.agents.keys()))
                output_state = self.agent_runner.run(
                    agent_name=agent_name, 
                    state=state, 
                    test_cases=test_cases, 
                    next_available_agents=next_available_agents)

                # Add the output state to memory
                self.memory_manager.add_message(agent_name, output_state.message)

                next_agents = output_state.next_agents
                continuing_agents, is_terminating = self._parse_agent_output(next_agents)
                
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

                    # if next_agent != END:
                    #     next_level_agents[next_agent].append(output_state)
                    # else:
                    #     terminating_states.append(output_state)
                    if next_agent == END:
                        terminating_states.append(output_state)
                
                # edge_rewards.append(edge_reward)
                for cont_n in continuing_agents:
                    next_level_agents[cont_n].append(output_state)

            if learn_terminating_only and self.termination_policy != 'all':
                break

            next_level_agents = {
                agent_name: self.memory_manager.merge_memory(states)
                for agent_name, states in next_level_agents.items()
            }
            if not next_level_agents:
                break
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
