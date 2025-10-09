from collections import defaultdict
from typing import Any, Dict, List, Tuple

from MetaFlow.config import Config
from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.flow.decision_space import DecisionSpace
from MetaFlow.utils.state import GeneralState


class Learner:
    def __init__(self, config: Config, decision_space: DecisionSpace, agents: Dict[str, BaseAgent]):
        self.config = config
        self.decision_space = decision_space
        self.agents = agents

    def learn(self, all_layers: List[Dict[str, GeneralState]], execution_trace: List[Tuple[str, str, float]]) -> None:
        """
        Backward propagation through the layers of the graph.
        """
        state_values = defaultdict(float)
        forward_graph = defaultdict(list)
        for u, v, _ in execution_trace:
            if v not in forward_graph:
                forward_graph[v].append(u)

        for layer in reversed(all_layers):
            for agent_name in layer:
                value = 0
                if agent_name in forward_graph:
                    outgoing_edges = [trace for trace in execution_trace if trace[0] == agent_name]
                    for _, next_agent, reward in outgoing_edges:
                        value += reward + self.decision_space.discount_factor * state_values[next_agent]
                state_values[agent_name] = value

        experiences = []
        for source_agent, target_agent, reward in execution_trace:
            # The DecisionSpace is responsible for the full Bellman update.
            # We only pass the immediate reward from the experience.
            experiences.append({
                "state": source_agent,
                "action": target_agent,
                "reward": reward,
            })

        self.decision_space.update_from_experience(experiences)
        self.decision_space.decay_epsilon()
        self.decision_space.save_q_table()
        self._update_agents_success_rate(execution_trace)

    def _update_agents_success_rate(self, execution_trace: List[Tuple[str, str, float]]) -> None:
        """
        Update the success rate of an agent.
        """
        success_agents = set()
        reverse_graph = defaultdict(list)
        end_parents = set()
        for source_agent, target_agent, _ in execution_trace:
            reverse_graph[target_agent].append(source_agent)
            if target_agent == "END":
                end_parents.add(source_agent)

        q = list(end_parents)
        visited = set(q)
        while q:
            agent = q.pop(0)
            success_agents.add(agent)
            for parent in reverse_graph.get(agent, []):
                if parent not in visited:
                    q.append(parent)
                    visited.add(parent)

        for agent in success_agents:
            if agent in self.agents:
                self.agents[agent].update_success_rate()