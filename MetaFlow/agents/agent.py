from typing import Dict, List

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.core.memory.document_manager import DocumentManager
from MetaFlow.core.memory.memory_processor import MemoryProcessor
from MetaFlow.utils.state import GeneralState
from MetaFlow.tools.file_tool import file_tree



class LLMAgent(BaseAgent):
    """
    A concrete base agent for all agents that primarily rely on an LLM to generate a response.
    It handles the logic of formatting prompts, calling the LLM, and parsing the standard output.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: List[Dict[str, str]] = None, config: Config = None):
        super().__init__(agent_name, agent_prompt, custom_tools, config)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        prompt = f"""
            Your Current Sub-Task: {state.sub_task}

            Current Project Structure: {file_tree('.')}

            Current Collaborative Document: {document_manager.get()}
            
            IMPORTANT: When using tools, focus on the technical implementation. 
            After tool execution, the system will automatically generate a complete summary 
            including all required output sections (<thinking>, <output>, <task_requirements>, etc.).
        """
        
        # Get managed memory and prepare chat history
        agent_memory = memory_processor.get_memory(self.agent_name)
        memory_history = []
        for msg in agent_memory:
            role = "system" if "<summary" in msg.output else 'assistant'
            memory_history.append({"role": role, "content": msg.output})

        # Get the standard prompt components
        system_inputs = self.get_prompt(
            task_description=state.task,
            prompt=prompt, 
            next_available_agents=next_available_agents
        )

        # Combine memory with the fresh prompt
        inputs = memory_history + system_inputs

        if self.custom_tools:
            raw_response = self.llm.chat_with_tools(messages=inputs, tools=self.custom_tools)
        else:
            raw_response = self.llm.chat(inputs)
            
        self.logger.info(f"==========LLMAgent {self.agent_name} output: {raw_response}")

        output_state = self._parse_response(raw_response, document_manager, state)

        return output_state