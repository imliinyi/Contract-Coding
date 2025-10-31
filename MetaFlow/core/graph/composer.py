from typing import Dict, List, Tuple

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.core.decision_space.decision_space import DecisionSpace
from MetaFlow.core.graph.traverser import GraphTraverser
from MetaFlow.core.memory.document_manager import DocumentManager
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.utils.state import GeneralState
                

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
        document_manager: DocumentManager
    ):
        super().__init__(agent_name, config)
        self.decision_space = decision_space
        self.agents = agents
        self.sub_graph = sub_graph
        self.document_manager = document_manager

    def _execute_agent(self, state: GeneralState, test_cases: List[str], 
            state_processor: MemoryProcessor, next_available_agents: List[str], document_manager: DocumentManager) -> GeneralState:
        """
        Executes the composite graph, then calls the LLM to summarize the results and decide the next step.
        """
        # final_states = self.composite_graph.run(state, test_cases, document_manager)
        traverser = GraphTraverser(
            config=self.config,
            agents=self.agents, 
            document_manager=self.document_manager,
            decision_space=self.decision_space,
            state_processor=state_processor
        )
        _, _, final_states = traverser.sub_traverse(
            sub_graph=self.sub_graph,
            initial_states=state,
            test_cases=test_cases
        )

        if not final_states:
            return GeneralState(
                task=state.task,
                sub_task=state.sub_task,
                role=self.agent_name,
                thinking=f"Failed to execute composite skill {self.agent_name}. No final states were produced.",
                output="No valid output state found.",
                task_requirements=state.task_requirements,
                next_agents=state.next_agents
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
        state, _ = self._parse_response(response_text, document_manager, state)

        return state

    
