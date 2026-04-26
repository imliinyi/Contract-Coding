import json
import os
import re
from typing import Dict, List, Optional

from pydantic import ValidationError

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
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
        target_file = None
        if state.sub_task:
            m = re.search(r"\b(?:Implement/Fix|Fix)\s+([^\s]+\.[A-Za-z0-9_]+)\b", state.sub_task)
            if m:
                target_file = m.group(1).strip()
        is_impl_agent = self.agent_name in {"Backend_Engineer", "Frontend_Engineer", "Algorithm_Engineer"}
        is_codex_backend = getattr(self.config, "MODEL_BACKEND", "openai").lower() == "codex_cli"
        if is_codex_backend and is_impl_agent and target_file:
            inputs.append(
                {
                    "role": "system",
                    "content": (
                        "Codex CLI is being used as a read-only model backend. Do not call write_file, "
                        "do not edit files directly, and do not claim that you edited files. Return the full "
                        f"implementation for '{target_file}' inside exactly one "
                        f"<file_write path=\"{target_file}\">...</file_write> block. The framework will validate "
                        "the target path and write that content after your response."
                    ),
                }
            )

        output_state = None
        while retry < 3:
            if self.custom_tools:
                raw_response = self.llm.chat_with_tools(messages=inputs, tools=self.custom_tools)
            else:
                raw_response = self.llm.chat(inputs)
            
            self.logger.info(f"==========LLMAgent {self.agent_name} output: {raw_response}")

            try:
                materialized = False
                if is_codex_backend and is_impl_agent and target_file:
                    materialized = self._materialize_codex_file_response(raw_response, target_file, fs)
                output_state = self._parse_response(raw_response, document_manager, state)
                # self.logger.info(f"==========Parsed Output State: {output_state}")
                if is_impl_agent and target_file:
                    abs_target = os.path.join(self.config.WORKSPACE_DIR, target_file)
                    if not os.path.exists(abs_target):
                        retry += 1
                        if is_codex_backend:
                            retry_requirement = (
                                f"Hard requirement: the framework could not materialize '{target_file}' from your response. "
                                f"Return the complete file content in <file_write path=\"{target_file}\">...</file_write>. "
                                "Do not use tool calls or arbitrary file paths."
                            )
                        else:
                            retry_requirement = (
                                f"Hard requirement: you did not create/update the required file '{target_file}'. "
                                f"You MUST call write_file with path='{target_file}' in this attempt. "
                                "Do not finish without creating the file and updating the document status to DONE."
                            )
                        inputs.append({"role": "user", "content": retry_requirement})
                        continue
                    if is_codex_backend and not materialized:
                        self.logger.warning(
                            "Codex CLI backend target file exists, but no matching <file_write> block was materialized."
                        )
                break
            except (json.JSONDecodeError, ValidationError, EmptyTaskRequirementsError) as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1
                continue

        return output_state

    def _materialize_codex_file_response(self, response_text: str, target_file: str, fs: WorkspaceFS) -> bool:
        """Write only the scheduler-selected target file from a Codex CLI text response."""
        content = self._extract_codex_file_content(response_text, target_file)
        if content is None:
            self.logger.warning(f"No valid <file_write> block found for Codex CLI target file: {target_file}")
            return False

        result = fs.write_file(target_file, content)
        if result.startswith("An error") or result.startswith("Error"):
            self.logger.error(f"Failed to materialize Codex CLI file response for {target_file}: {result}")
            return False

        self.logger.info(f"Materialized Codex CLI generated code for {target_file}: {result}")
        return True

    @staticmethod
    def _extract_codex_file_content(response_text: str, target_file: str) -> Optional[str]:
        if not response_text or not target_file:
            return None

        target_norm = LLMAgent._normalize_contract_path(target_file)
        file_write_pattern = re.compile(
            r"<file(?:_write)?\s+path=[\"']([^\"']+)[\"']\s*>(.*?)</file(?:_write)?>",
            re.DOTALL | re.IGNORECASE,
        )
        matches = list(file_write_pattern.finditer(response_text))
        for match in matches:
            candidate_path = LLMAgent._normalize_contract_path(match.group(1))
            if candidate_path == target_norm:
                return LLMAgent._strip_code_fence(match.group(2).strip())

        if matches:
            return None

        fenced_blocks = re.findall(r"```[A-Za-z0-9_+.-]*\s*\n(.*?)\n```", response_text, re.DOTALL)
        if len(fenced_blocks) == 1:
            return fenced_blocks[0].strip()
        return None

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        fence = re.match(r"^```[A-Za-z0-9_+.-]*\s*\n(.*?)\n```$", content, re.DOTALL)
        if fence:
            return fence.group(1).strip()
        return content.strip()

    @staticmethod
    def _normalize_contract_path(path: str) -> str:
        normalized = (path or "").strip().strip("'\"").replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith("workspace/"):
            normalized = normalized[len("workspace/"):]
        return os.path.normcase(os.path.normpath(normalized)).replace("\\", "/")
