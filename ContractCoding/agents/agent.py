import json
import os
import re
from typing import Dict, List

from pydantic import ValidationError

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.workspace_context import get_current_workspace
from ContractCoding.tools.file_tool import WorkspaceFS
from ContractCoding.utils.exception import EmptyTaskRequirementsError
from ContractCoding.utils.state import GeneralState


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
        runtime_workspace = get_current_workspace(self.config.WORKSPACE_DIR)
        fs = WorkspaceFS(runtime_workspace)
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
        target_files: List[str] = []
        if state.sub_task:
            lines = state.sub_task.splitlines()
            collecting_targets = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Target files in this module wave:"):
                    collecting_targets = True
                    continue
                if collecting_targets:
                    if not stripped and target_files:
                        break
                    bullet_match = re.match(r"[-*]\s+`?([^`]+?\.[A-Za-z0-9_]+)`?$", stripped)
                    if bullet_match:
                        target_files.append(bullet_match.group(1).strip())
                        continue
                    if target_files:
                        break
            if not target_files:
                m = re.search(r"\b(?:Implement/Fix|Fix)\s+([^\s]+\.[A-Za-z0-9_]+)\b", state.sub_task)
                if m:
                    target_files.append(m.group(1).strip())
        is_impl_agent = self.agent_name in {"Backend_Engineer", "Frontend_Engineer", "Algorithm_Engineer"}
        while retry < 3:
            if self.custom_tools:
                raw_response = self.llm.chat_with_tools(messages=inputs, tools=self.custom_tools)
            else:
                raw_response = self.llm.chat(inputs)
            
            self.logger.info(f"==========LLMAgent {self.agent_name} output: {raw_response}")

            try:
                output_state = self._parse_response(raw_response, document_manager, state)
                # self.logger.info(f"==========Parsed Output State: {output_state}")
                if is_impl_agent and target_files:
                    missing_targets = []
                    for target_file in target_files:
                        abs_target = os.path.join(runtime_workspace, target_file)
                        if not os.path.exists(abs_target):
                            missing_targets.append(target_file)
                    if missing_targets:
                        retry += 1
                        inputs.append(
                            {
                                "role": "user",
                                "content": (
                                    "Hard requirement: you did not create/update all required files for this run. "
                                    f"Missing targets: {', '.join(missing_targets)}. "
                                    "You MUST create or update every missing file in this attempt and update the corresponding document status to DONE."
                                ),
                            }
                        )
                        continue
                break
            except (json.JSONDecodeError, ValidationError, EmptyTaskRequirementsError) as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1
                continue

        return output_state
