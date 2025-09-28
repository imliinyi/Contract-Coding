import re

from pydantic import BaseModel
from typing import List, Dict
from collections import defaultdict

from DAGAgent.agents.base_agent import BaseAgent
from DAGAgent.DAG.memory import MemoryManager
from DAGAgent.config import Config
from DAGAgent.utils.state import Message, GeneralState
from DAGAgent.utils.coding.python_executor import execute_code_get_return
from DAGAgent.utils.math.get_predict import get_predict


class CompositeGraph(BaseModel):
    """
    CompositeGraph class for the DAGAgent.
    """
    def __init__(self, agent_name: str, config: Config, sub_graph: Dict[str, List[str]], agents: Dict[str, BaseAgent]):
        self.agent_name = agent_name
        self.config = config
        self.sub_graph = sub_graph
        self.agents = agents
        self.memory_manager = MemoryManager(self.config, self.config.MEMORY_WINDOW)

    def run(self, initial_state: GeneralState, test_cases: List[Dict[str, str]]) -> List[GeneralState]:
        """
        Execute the agent.
        """
        entry_points = self.sub_graph.get('START', [])
        if not entry_points:
            return []

        layer_states = {agent_name: initial_state for agent_name in set(entry_points)}
        terminating_states = []
        visited_agents = set()

        while layer_states:
            next_layer_inputs = defaultdict(list)

            current_agents = set(layer_states.keys())
            if current_agents.issubset(visited_agents):
                # All agents in the current layer have been visited
                break
            visited_agents.update(current_agents)

            for agent_name, state in layer_states.items():
                agent = self.agents.get(agent_name, None)
                if not agent:
                    continue

                message = agent._execute_agent(state, test_cases, next_available_agents=[])

                # Extract code from the message output
                code_pattern = r'```python\n(.*?)```'
                code_match = re.search(code_pattern, message.output, re.DOTALL)
                code = code_match.group(1).strip() if code_match else ''

                if code:
                    answer = execute_code_get_return(code)
                else:
                    answer = get_predict(message.output)

                output_state = GeneralState(
                    task=state.task,
                    code=code if code else state.code,
                    answer=answer,
                    messages=message,
                    next_agents=[]
                )

                successors = self.sub_graph.get(agent_name, [])
                for successor_name in successors:
                    if successor_name == "END":
                        terminating_states.append(output_state)
                    else:
                        next_layer_inputs[successor_name].append(output_state)

            next_layer_states = {}
            for agent_name, states in next_layer_inputs.items():
                if len(states) > 0:
                    next_layer_states[agent_name] = self.memory_manager.merge_message(states)
                elif states:
                    next_layer_states[agent_name] = states[0]

            layer_states = next_layer_states

        return terminating_states


class CompositeAgent(BaseAgent):
    """
    CompositeAgent class for the DAGAgent.
    """
    def __init__(self, agent_name: str, config: Config, sub_graph: Dict[str, List[str]], agents: Dict[str, BaseAgent]):
        super().__init__(agent_name, config)
        self.composite_graph = CompositeGraph(agent_name, config, sub_graph, agents)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Message:
        """
        Execute the agent.
        """
        final_states = self.composite_graph.run(state, test_cases)

        if final_states:
            final_outputs = [state.message for state in final_states if state.message]
            merged_output = "\n --- Sub-Task Output --- \n".join(final_outputs)

            return Message(
                role=self.agent_name,
                thinking=f"Successfully executed composite skill {self.agent_name}",
                output=merged_output
            )
        else:
            return Message(
                role=self.agent_name,
                thinking=f"Failed to execute composite skill {self.agent_name}",
                output="No valid output state found."
            )

    
