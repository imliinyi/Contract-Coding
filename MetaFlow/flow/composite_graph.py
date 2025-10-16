import re

from numpy.random import f
from pydantic import BaseModel
from typing import List, Dict, Tuple
from collections import defaultdict

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.flow.agent_runner import AgentRunner
from MetaFlow.flow.graph_traverser import GraphTraverser
from MetaFlow.flow.decision_space import DecisionSpace
from MetaFlow.flow.memory import MemoryManager
# from MetaFlow.prompt.system_prompt import COMPOSITE_AGENT_PROMPT
from MetaFlow.config import Config
from MetaFlow.utils.state import Message, GeneralState


class CompositeGraph(BaseModel):
    """
    Represents the structure and execution of a pre-defined subgraph.
    """ 
    def __init__(
        self, agent_name: str, 
        config: Config, 
        decision_space: DecisionSpace,
        sub_graph: List[Tuple[str, str]], 
        agents: Dict[str, BaseAgent],
        agent_runner: AgentRunner
    ):
        self.agent_name = agent_name
        self.config = config
        self.decision_space = decision_space
        self.sub_graph = sub_graph
        self.agents = agents
        self.agent_runner = agent_runner
        self.memory_manager = MemoryManager(self.config, self.config.MEMORY_WINDOW)

    def run(self, initial_state: GeneralState, test_cases: List[Dict[str, str]]) -> List[GeneralState]:
        """
        Executes the predefined subgraph by delegating to a GraphTraverser.
        """
        traverser = GraphTraverser(
            config=self.config,
            agents=self.agents, 
            decision_space=self.decision_space,
            agent_runner=self.agent_runner, 
            memory_manager=self.memory_manager
        )
        _, _, terminating_states = traverser.sub_traverse(
            sub_graph=self.sub_graph,
            initial_states=initial_state,
            test_cases=test_cases,
        )
        # entry_points = self.sub_graph.get('START', [])
        # if not entry_points:
        #     return []

        # current_layer_states = {agent_name: initial_state for agent_name in set(entry_points)}
        # terminating_states = []
        # executed_agents = []

        # while current_layer_states:
        #     current_agent_set = frozenset(current_layer_states.keys())
        #     if current_agent_set in executed_agents:
        #         break   # Prevent infinite loops
        #     executed_agents.append(current_agent_set)
        #     next_layer_inputs = defaultdict(list)

        #     for agent_name, input_state in current_layer_states.items():
        #         agent = self.agents.get(agent_name, None)
        #         if not agent:
        #             continue

        #         state = self.agent_runner.run(agent_name, input_state, test_cases, [])

        #         successors = self.sub_graph.get(agent_name, [])
        #         for successor in successors:
        #             if successor == 'END':
        #                 terminating_states.append(state)
        #             else:
        #                 next_layer_inputs[successor].append(state)

        #     next_layer_states = {}
        #     for agent_name, states in next_layer_inputs.items():
        #         if len(states) > 1:
        #             merged_state = self.memory_manager.merge_memory(states)
        #             next_layer_states[agent_name] = merged_state
        #         else:
        #             next_layer_states[agent_name] = states[0]

        #     current_layer_states = next_layer_states

        return terminating_states
                

class CompositeAgent(BaseAgent):
    """
    An agent that encapsulates a composite graph (a pre-defined skill).
    """
    def __init__(
        self, 
        agent_name: str, 
        config: Config, 
        decision_space: DecisionSpace,
        sub_graph: List[Tuple[str, str]], 
        agents: Dict[str, BaseAgent],
        agent_runner: AgentRunner
    ):
        super().__init__(agent_name, config)
        self.composite_graph = CompositeGraph(
            agent_name=agent_name, 
            config=config, 
            decision_space=decision_space,
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

        # Summarize the final outputs of the subgraph to form the user prompt
        summary_header = f"The composite skill '{self.agent_name}' has completed. Here is a summary of its final outputs:"
        final_outputs = [f"- {s.message.output}" for s in final_states if s.message and s.message.output]
        summary = summary_header + "\n" + "\n".join(final_outputs)
        user_prompt = f"{summary}\n\nBased on this summary, what is the next logical step to solve the overall task: {state.task}"

        # Use the standard prompt structure for the summary decision, aligning with BaseAgent.
        # This separates system instructions from user-provided context for better model performance.
        inputs = self.get_prompt(
            task_description=state.task,
            sys_prompt=self.get_system_prompt(),
            agent_prompt=f"You are a project manager overseeing the task. Your role is to decide the next step after a sub-task ('{self.agent_name}') has finished.",
            prompt=user_prompt,
            next_available_agents=next_available_agents,
        )

        response_text = self.llm.chat(inputs)

        thinking = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        output = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)

        return Message(
            role=self.agent_name,
            thinking=thinking.group(1).strip() if thinking else "",
            output=output.group(1).strip() if output else response_text,
        )

    
