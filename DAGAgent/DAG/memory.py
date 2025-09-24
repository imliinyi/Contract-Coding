from typing import List
from DAGAgent.utils.state import Message
from DAGAgent.llm.llm import LLM
from DAGAgent.config import Config


class MemoryManager:
    def __init__(self, config: Config, memory_window: int):
        self.llm = LLM(
            deployment_name=config.OPENAI_API_DEPLOYMENT_NAME,
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_API_BASE_URL,
            max_tokens=config.OPENAI_API_MAX_TOKENS,
            temperature=config.OPENAI_API_TEMPERATURE,
        )
        self.memory_window = memory_window
        self.memory: List[Message] = []

    def add_message(self, message: Message):
        self.memory.append(message)
        if len(self.memory) > self.memory_window:
            self.memory.pop(0)

    def format_message(self, message: Message, task: str) -> str:
        """
        Formats a message for the LLM, including the task and the message content.
        """
        thinking_section = ""
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
            {thinking_section}
            {output_section}\n
            ---\n
            ### Your Task:
            {task}
        """
        
    def merge_memory(self, states: List[Message]) -> Message:
        """
        Merges a list of messages from multiple upstream agents into
         a single message.
        """
        if not states:
            return Message(role="system", thinking="", output="")

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

        # The merged message represents the combined input for the next agent.
        # The role is 'user' as it serves as the prompt/input for the next step.
        return Message(role="system", thinking=merged_thinking, output=merged_output)   
