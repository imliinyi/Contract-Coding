import re
from typing import Any, Dict, List, Optional, Tuple

from MetaFlow.config import Config
from MetaFlow.flow.document_manager import DocumentManager
from MetaFlow.llm.llm import LLM
from MetaFlow.utils.coding.python_executor import execute_code_get_return
from MetaFlow.utils.math.get_predict import get_predict
from MetaFlow.utils.state import GeneralState, Message


class StateProcessor:
    def __init__(self, config: Config, agents: List[str], memory_window: int=5):
        self.llm = LLM(
            deployment_name=config.OPENAI_DEPLOYMENT_NAME,
            api_key=config.OPENAI_API_KEY,
            api_base=config.OPENAI_API_BASE_URL,
            max_tokens=config.OPENAI_API_MAX_TOKENS,
            temperature=config.OPENAI_API_TEMPERATURE,
        )
        self.agents = agents
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

    def _normalize_agent_name(self, agent_name: str) -> str:
        """
        Normalize the agent name to a registered name.
        """
        # Create a mapping from lowercase, underscore-removed names to original names
        normalized_map = {re.sub(r'[^a-z0-9]', '', name.lower()): name for name in self.agents}
        
        # Normalize the input name
        normalized_input = re.sub(r'[^a-z0-9]', '', agent_name.lower())
        
        return normalized_map.get(normalized_input, agent_name) # Return original if not found

    def process_agent_output(
        self, 
        message: Message, 
        current_state: GeneralState
    ) -> GeneralState:
        """
        Process the output of an agent, creating a new GeneralState for the next agent.
        This includes extracting and executing code, getting an answer, and normalizing agent names.
        """
        # Extract code from the message output
        code_pattern = r'```python\n(.*?)```'
        code_match = re.search(code_pattern, message.output, re.DOTALL)
        code = code_match.group(1).strip() if code_match else ''

        if code:
            answer = execute_code_get_return(code)
        else:
            answer = get_predict(message.output)
        if not answer:
            answer = current_state.answer or ""

        # Normalize the next_agents names
        if message.next_agents:
            normalized_next_agents = [self._normalize_agent_name(name) for name in message.next_agents]
            message.next_agents = normalized_next_agents
        
        return GeneralState(
            task=current_state.task,
            sub_task=current_state.sub_task,
            code=code,
            answer=answer,
            message=message
        )

    def format_state(self, state: GeneralState, document_manager: DocumentManager) -> str:
        """
        Formats a message for the LLM, including the task and the message content.
        """
        collaborative_document = document_manager.get()
        collaborative_document_section = ""
        if collaborative_document:
            collaborative_document_section = f"""
                ### Collaborative Document:
                {collaborative_document}
            """

        output_section = ""
        if state.message.output:
            output_section = f"""
                ### Previous Agent's Output:
                <output>
                {state.message.output}
                </output>
            """

        return f"""
            ### Current Collaborative Document:
            {collaborative_document_section}\n
            ### Previous Agent's Output:
            {output_section}\n
            ---\n
            ### User Task:
            {state.task}
            ### Your Task:
            {state.sub_task}
        """
        
    # def merge_message(self, states: List[Message]) -> Message:
    #     """
    #     Merges a list of messages from multiple upstream agents into
    #      a single message.
    #     """
    #     if not states:
    #         return Message(
    #             role="system", 
    #             thinking="", 
    #             output="", 
    #             next_agents=[], 
    #             task_requirements=None
    #         )

    #     if len(states) == 1:
    #         return states[0]

    #     # Use a structured format to combine outputs and thoughts.
    #     # A clear separator helps the LLM distinguish between different inputs.
    #     separator = "\n\n---\n[END OF UPSTREAM INPUT]\n---\n\n"

    #     merged_output = []
    #     merged_thinking = []

    #     for i, state in enumerate(states):
    #         header = f"[Input from Upstream Agent {i + 1}]"
    #         # Append thinking if it exists
    #         if state.thinking:
    #             merged_thinking.append(f"{header}\n{state.thinking}")
    #         # Append output
    #         if state.output:
    #             merged_output.append(f"{header}\n{state.output}")
            
    #     # Join the parts with the separator
    #     merged_output = separator.join(merged_output)
    #     merged_thinking = separator.join(merged_thinking)
    #     next_agents = []
    #     task_requirements = {}
    #     for state in states:
    #         if state.next_agents:
    #             next_agents.extend(state.next_agents)
    #         if state.task_requirements:
    #             for key, value in state.task_requirements.items():
    #                 task_requirements[key] = task_requirements.get(key, "")  + '\n' + value

    #     next_agents = list(set(next_agents))

    #     # The merged message represents the combined input for the next agent.
    #     # The role is 'user' as it serves as the prompt/input for the next step.
    #     return Message(
    #         role="system", 
    #         thinking=merged_thinking, 
    #         output=merged_output, 
    #         next_agents=next_agents, 
    #         task_requirements=task_requirements
    #     )   
    def _merge_messages(self, messages: List[Message]) -> Message:
        """
        Merges a list of messages from multiple upstream agents into a single message.
        """
        if not messages:
            return Message(role="system", output="")

        if len(messages) == 1:
            return messages[0]

        separator = "\n\n---\n[Input from another agent]\n---\n\n"
        
        merged_output = separator.join([msg.output for msg in messages if msg.output])
        merged_thinking = separator.join([msg.thinking for msg in messages if msg.thinking])
        merged_next_agents = []
        merged_task_requirements = {}
        for msg in messages:
            if msg.next_agents:
                merged_next_agents.extend(msg.next_agents)
            if msg.task_requirements:
                for key, value in msg.task_requirements.items():
                    merged_task_requirements[key] = merged_task_requirements.get(key, "")  + '\n' + value

        merged_next_agents = list(set(merged_next_agents))

        return Message(
            role="assistant",
            thinking=merged_thinking,
            output=merged_output,
            next_agents=merged_next_agents,
            task_requirements=merged_task_requirements
        )

    def merge_memory(self, states: List[GeneralState]) -> GeneralState:
        """
        Merges a list of states from multiple upstream agents into
         a single state.
        """
        if not states:
            return GeneralState(
                    message=Message(role="system", thinking="", output=""), 
                    sub_task="", 
                    task="", 
                    code="", 
                    answer="",
                )
        
        if len(states) == 1:
            return states[0]

        merged_messages = self._merge_messages([state.message for state in states])

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
            task=states[0].task, 
            code=merged_code,
            answer=merged_answer,
            message=merged_messages
        )
