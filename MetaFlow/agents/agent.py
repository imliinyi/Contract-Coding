import json
import re
from typing import Dict, List

from pydantic import ValidationError

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.memory.document import DocumentManager
from MetaFlow.memory.processor import MemoryProcessor
from MetaFlow.tools.file_tool import WorkspaceFS
from MetaFlow.utils.exception import EmptyTaskRequirementsError
from MetaFlow.utils.state import GeneralState


class LLMAgent(BaseAgent):
    """
    A concrete base agent for all agents that primarily rely on an LLM to generate a response.
    It handles the logic of formatting prompts, calling the LLM, and parsing the standard output.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: List[Dict[str, str]] = None, config: Config = None):
        super().__init__(agent_name, agent_prompt, custom_tools, config)

    def _execute_agent(self, state: GeneralState, document_manager: DocumentManager, 
        memory_processor: MemoryProcessor, next_available_agents: List[str]) -> GeneralState:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        fs = WorkspaceFS(self.config.WORKSPACE_DIR)
        prompt = f"""
            Current Task: {state.sub_task}

            Current Project Structure:\n {fs.file_tree('.', max_depth=3)}

            Current Collaborative Document:\n {document_manager.get()}
            
            IMPORTANT: When using tools, focus on the technical implementation. 
            After tool execution, you MUST generate a <document_action>.
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
        # inputs = system_inputs
        retry = 0
        while retry < 3:
            if self.custom_tools:
                raw_response = self.llm.chat_with_tools(messages=inputs, tools=self.custom_tools)
            else:
                raw_response = self.llm.chat(inputs)
            
            logged_response = re.sub(
                r"<task_requirements>[\s\S]*?</task_requirements>",
                "<task_requirements>[OMITTED]</task_requirements>",
                raw_response,
            )
            self.logger.info(f"==========LLMAgent {self.agent_name} output: {logged_response}")

            try:
                output_state = self._parse_response(raw_response, document_manager, state)
                # self.logger.info(f"==========Parsed Output State: {output_state}")
                break
            except (json.JSONDecodeError, ValidationError, EmptyTaskRequirementsError) as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1
                continue

        return output_state
