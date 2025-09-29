import re

from pydantic import BaseModel
from typing import List, Dict
from collections import defaultdict

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.flow.agent_runner import AgentRunner
from MetaFlow.flow.memory import MemoryManager
from MetaFlow.prompt.system_prompt import SYSTEM_PROMPT, AGENT_PROMPT
from MetaFlow.config import Config
from MetaFlow.utils.state import Message, GeneralState
from MetaFlow.utils.coding.python_executor import execute_code_get_return
from MetaFlow.utils.math.get_predict import get_predict


class CompositeGraph(BaseModel):
    """
    CompositeGraph class for the DAGAgent.
    """
    def __init__(
        self, agent_name: str, 
        config: Config, 
        sub_graph: Dict[str, List[str]], 
        agents: Dict[str, BaseAgent],
        agent_runner: AgentRunner
    ):
        self.agent_name = agent_name
        self.config = config
        self.sub_graph = sub_graph
        self.agents = agents
        self.agent_runner = agent_runner
        self.memory_manager = MemoryManager(self.config, self.config.MEMORY_WINDOW)

    def run(self, initial_state: GeneralState, test_cases: List[Dict[str, str]]) -> List[GeneralState]:
        """
        Execute the agent.
        """
        entry_points = self.sub_graph.get('START', [])
        if not entry_points:
            return []

        current_layer_states = {agent_name: initial_state for agent_name in set(entry_points)}
        terminating_states = []
        executed_agents = []

        while current_layer_states:
            current_agent_set = frozenset(current_layer_states.keys())
            if current_agent_set in executed_agents:
                break   # Prevent infinite loops
            executed_agents.append(current_agent_set)
            next_layer_inputs = defaultdict(list)

            for agent_name, input_state in current_layer_states.items():
                agent = self.agents.get(agent_name, None)
                if not agent:
                    continue

                state = self.agent_runner.run(agent_name, input_state, test_cases, [])

                successors = self.sub_graph.get(agent_name, [])
                for successor in successors:
                    if successor == 'END':
                        terminating_states.append(state)
                    else:
                        next_layer_inputs[successor].append(state)

            next_layer_states = {}
            for agent_name, states in next_layer_inputs.items():
                if len(states) > 1:
                    merged_state = self.memory_manager.merge_memory(states)
                    next_layer_states[agent_name] = merged_state
                else:
                    next_layer_states[agent_name] = states[0]

            current_layer_states = next_layer_states

        return terminating_states
                

class CompositeAgent(BaseAgent):
    """
    CompositeAgent class for the DAGAgent.
    """
    def __init__(
        self, 
        agent_name: str, 
        config: Config, 
        sub_graph: Dict[str, List[str]], 
        agents: Dict[str, BaseAgent],
        agent_runner: AgentRunner
    ):
        super().__init__(agent_name, config)
        self.composite_graph = CompositeGraph(
            agent_name=agent_name, 
            config=config, 
            sub_graph=sub_graph, 
            agents=agents, 
            agent_runner=agent_runner
        )

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Message:
        """
        Executes the composite graph, then calls the LLM to summarize the results and decide the next step.
        """
        final_states = self.composite_graph.run(state, test_cases)

        if not final_states:
            return Message(
                role=self.agent_name,
                thinking=f"Failed to execute composite skill {self.agent_name}. No final states were produced.",
                output="No valid output state found."
            )

        # Summarize the final outputs of the subgraph
        summary_header = f"The composite skill '{self.agent_name}' has completed. Here is a summary of its final outputs:"
        final_outputs = [f"- {s.message.output}" for s in final_states if s.message and s.message.output]
        summary = summary_header + "\n" + "\n".join(final_outputs)

        # Ask the LLM to decide the next step based on the summary
        # This reuses the main system prompt for a consistent decision-making process
        avail_agents_details = ', '.join(f"{name}: {AGENT_PROMPT.get(name, 'N/A')}" for name in next_available_agents)
        prompt = SYSTEM_PROMPT.format(
            avail_agents_details=avail_agents_details,
            task_description=f"Based on the summary of your previous action, what is the next logical step to solve the overall task: {state.task}",
            previous_steps=summary
        )

        response_text = self.llm.chat([{"role": "user", "content": prompt}])

        thinking = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        output = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)

        return Message(
            role=self.agent_name,
            thinking=thinking.group(1).strip() if thinking else "",
            output=output.group(1).strip() if output else response_text,
        )

    
