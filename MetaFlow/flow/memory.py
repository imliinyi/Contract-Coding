from ast import Dict
from typing import Dict, List

from MetaFlow.config import Config
from MetaFlow.llm.llm import LLM
from MetaFlow.utils.state import GeneralState, Message


class MemoryManager:
    def __init__(self, config: Config, memory_window: int=5):
        self.llm = LLM(
            deployment_name=config.OPENAI_DEPLOYMENT_NAME,
            api_key=config.OPENAI_API_KEY,
            api_base=config.OPENAI_API_BASE_URL,
            max_tokens=config.OPENAI_API_MAX_TOKENS,
            temperature=config.OPENAI_API_TEMPERATURE,
        )
        self.memory_window = memory_window
        self.memory: Dict[str, List[Message]] = {}

    def add_message(self, agent_name: str, message: Message):
        """
        Adds a message to the memory of the specified agent.
        """
        if agent_name not in self.memory:
            self.memory[agent_name] = []
        self.memory[agent_name].append(message)
        if len(self.memory[agent_name]) > self.memory_window:
            self.memory[agent_name].pop(0)

    def transfer_message(self, task: str, code: str, sub_task: str, shared_context: str,
                        answer: str, message: Message) -> GeneralState:
        """
        Transfers a message from one agent to the next, updating the task, code, answer, and next_agents.
        """
        return GeneralState(
                message=message, 
                sub_task=sub_task, 
                shared_context=shared_context,
                task=task, 
                code=code, 
                answer=answer,
            )

    def format_state(self, state: GeneralState) -> str:
        """
        Formats a message for the LLM, including the task and the message content.
        """
        # Add the shared context
        shared_context_section = ""
        if state.shared_context:
            shared_context_section = f"""
                ### Shared Context:
                {state.shared_context}
            """

        thinking_section = ""
        message = state.message
        if message.thinking:
            thinking_section = f"""
                ### Previous Agent's Thought:
                <thinking>
                {message.thinking}
                </thinking>
            """
        
        # Add the final output from previous step
        output_section = ""
        if message.output:
            output_section = f"""
                ### Previous Agent's Output:
                <output>
                {message.output}
                </output>
            """

        # Combine all sections
        return f"""
            ### Current programme:
            {shared_context_section}
            ### Previous Agent's Thought:
            {thinking_section}
            ### Previous Agent's Output:
            {output_section}\n
            ---\n
            ### User Task:
            {state.task}
            ### Your Task:
            {state.sub_task}
        """
        
    def merge_message(self, states: List[Message]) -> Message:
        """
        Merges a list of messages from multiple upstream agents into
         a single message.
        """
        if not states:
            return Message(
                role="system", 
                thinking="", 
                output="", 
                next_agents=[], 
                task_requirements=None
            )

        if len(states) == 1:
            return states[0]

        # Use a structured format to combine outputs and thoughts.
        # A clear separator helps the LLM distinguish between different inputs.
        separator = "\n\n---\n[END OF UPSTREAM INPUT]\n---\n\n"

        merged_output = []
        merged_thinking = []

        for i, state in enumerate(states):
            header = f"[Input from Upstream Agent {i + 1}]"
            # Append thinking if it exists
            if state.thinking:
                merged_thinking.append(f"{header}\n{state.thinking}")
            # Append output
            if state.output:
                merged_output.append(f"{header}\n{state.output}")
            
        # Join the parts with the separator
        merged_output = separator.join(merged_output)
        merged_thinking = separator.join(merged_thinking)
        next_agents = []
        task_requirements = {}
        for state in states:
            if state.next_agents:
                next_agents.extend(state.next_agents)
            if state.task_requirements:
                for key, value in state.task_requirements.items():
                    task_requirements[key] = task_requirements.get(key, [])  + '\n' + value

        next_agents = list(set(next_agents))

        # The merged message represents the combined input for the next agent.
        # The role is 'user' as it serves as the prompt/input for the next step.
        return Message(
            role="system", 
            thinking=merged_thinking, 
            output=merged_output, 
            next_agents=next_agents, 
            task_requirements=task_requirements
        )   

    def merge_memory(self, states: List[GeneralState]) -> GeneralState:
        """
        Merges a list of states from multiple upstream agents into
         a single state.
        """
        if not states:
            # If no states are provided, return an empty state
            return GeneralState(
                    message=Message(role="system", thinking="", output=""), 
                    sub_task="", 
                    task="", 
                    code="", 
                    answer="",
                )
        
        if len(states) == 1:
            # If only one state is provided, return it as is
            return states[0]

        merged_messages = self.merge_message([state.message for state in states])

        separator = "\n\n" + "="*20 + " MERGED INPUT " + "="*20 + "\n\n"
        merged_code = separator.join(
            f"# Code from Upstream Agent {i + 1}:\n{state.code}"
            for i, state in enumerate(states) if state.code
        )
        
        merged_sub_task = separator.join(
            f"# Sub Task from Upstream Agent {i + 1}:\n{state.sub_task}"
            for i, state in enumerate(states) if state.sub_task
        )
        merged_answer = separator.join(
            f"# Answer from Upstream Agent {i + 1}:\n{state.answer}"
            for i, state in enumerate(states) if state.answer
        )

        # Create a new state with the merged message and code/answer
        return GeneralState(
            sub_task=merged_sub_task,
            task=states[0].task,  # Assuming task is the same for all states
            code=merged_code,
            answer=merged_answer,
            message=merged_messages
        )
