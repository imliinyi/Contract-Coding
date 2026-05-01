import os
import re
from typing import Dict, List

from pydantic import ValidationError

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.execution.workspace import get_current_workspace
from ContractCoding.llm.observability import response_observability
from ContractCoding.tools.file_tool import WorkspaceFS
from ContractCoding.utils.state import GeneralState


class LLMAgent(BaseAgent):
    """
    A concrete base agent for all agents that primarily rely on an LLM to generate a response.
    It handles the logic of formatting prompts, calling the LLM, and parsing the standard output.
    """
    def __init__(self, agent_name: str, agent_prompt: str, custom_tools: List[Dict[str, str]] = None, config: Config = None):
        super().__init__(agent_name, agent_prompt, custom_tools, config)

    def _execute_agent(self, state: GeneralState,
        context_manager: ContextManager, next_available_agents: List[str]) -> GeneralState:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        runtime_workspace = get_current_workspace(self.config.WORKSPACE_DIR)
        fs = WorkspaceFS(runtime_workspace)
        prompt = f"""
            Current Agent Input Packet and Task Slice:\n {state.sub_task}

            Current Project Structure:\n {fs.file_tree('.', max_depth=3)}

            IMPORTANT: When using tools, focus on the technical implementation. 
            ContractCoding records work-item status and evidence outside the model response.
            You only see the contract slice relevant to this step; do not infer permission
            to edit artifacts outside the packet's allowed_artifacts/conflict_keys.
        """
        
        memory_history = context_manager.build_message_history(self.agent_name)

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
        target_files, conflict_keys, allowed_tools = self._extract_artifact_policy(state.sub_task or "")
        is_file_writer_agent = self._agent_enforces_target_completion(self.agent_name)
        while retry < 3:
            backend = self.backend
            if hasattr(backend, "workspace_dir"):
                backend.workspace_dir = runtime_workspace
            if hasattr(backend, "allowed_artifacts"):
                backend.allowed_artifacts = list(target_files)
            if hasattr(backend, "allowed_conflict_keys"):
                backend.allowed_conflict_keys = conflict_keys or [f"artifact:{path}" for path in target_files]
            if hasattr(backend, "allowed_tools"):
                backend.allowed_tools = list(allowed_tools)
            if hasattr(backend, "repair_diagnostics_text"):
                backend.repair_diagnostics_text = self._extract_repair_diagnostics(state.sub_task)
            if self.custom_tools:
                response = backend.chat_with_tools(messages=inputs, tools=self.custom_tools)
            else:
                response = backend.chat(inputs)
            raw_response = response.content
            
            self.logger.info(f"==========LLMAgent {self.agent_name} output: {raw_response}")

            try:
                output_state = self._parse_response(raw_response, state)
                self._attach_backend_observability(output_state, backend, response)
                # self.logger.info(f"==========Parsed Output State: {output_state}")
                if is_file_writer_agent and target_files:
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
                                    "You MUST create or update every missing file in this attempt and report concrete evidence."
                                ),
                            }
                        )
                        continue
                break
            except ValidationError as e:
                self.logger.error(f"Attempt {retry + 1} failed with parsing error: {e}")
                retry += 1
                continue

        return output_state

    @staticmethod
    def _attach_backend_observability(output_state: GeneralState, backend, response) -> None:
        requirements = dict(output_state.task_requirements or {})
        observed = response_observability(response, backend)
        requirements["llm_observability"] = observed
        raw = response.raw if isinstance(response.raw, dict) else {}
        terminal = dict(raw.get("terminal_result", {}) or {})
        if terminal:
            requirements["agent_terminal"] = terminal
        output_state.task_requirements = requirements

    @staticmethod
    def _infer_work_kind(sub_task: str) -> str:
        match = re.search(r"^Kind:\s*([A-Za-z0-9_-]+)\s*$", sub_task or "", re.MULTILINE)
        if match:
            return match.group(1).strip().lower()
        if re.search(r"\b(?:Implement/Fix|Target files in this module wave|Target files:|Target artifacts:|Wave allowed artifacts:)\b", sub_task or ""):
            return "coding"
        return ""

    @staticmethod
    def _extract_repair_diagnostics(sub_task: str) -> str:
        text = sub_task or ""
        marker = "Repair diagnostics:"
        index = text.find(marker)
        if index < 0:
            return ""
        tail = text[index:]
        stop = tail.find("\nProvided interfaces:")
        return tail[:stop].strip() if stop >= 0 else tail.strip()

    @staticmethod
    def _agent_enforces_target_completion(agent_name: str) -> bool:
        return agent_name in {
            "Backend_Engineer",
            "Frontend_Engineer",
            "Algorithm_Engineer",
            "Test_Engineer",
            "Recovery_Orchestrator",
        }

    @classmethod
    def _extract_artifact_policy(cls, sub_task: str) -> tuple[List[str], List[str], List[str]]:
        target_files: List[str] = []
        conflict_keys: List[str] = []
        allowed_tools: List[str] = []

        def add_unique(values: List[str], raw_value: str) -> None:
            normalized = raw_value.strip().strip("`'\"[] ")
            if normalized and normalized != "None" and normalized not in values:
                values.append(normalized)

        collecting: str = ""
        for line in (sub_task or "").splitlines():
            stripped = line.strip()
            if stripped in {
                "Target files in this module wave:",
                "Target files:",
                "Target artifacts:",
                "Wave allowed artifacts:",
                "Files to review:",
            }:
                collecting = "targets"
                continue
            if stripped in {"Conflict keys:", "- conflict_keys:"}:
                collecting = "conflict_keys"
                continue
            if stripped in {"Allowed tools:", "- allowed_tools:"}:
                collecting = "allowed_tools"
                continue
            if collecting:
                if not stripped:
                    collecting = ""
                    continue
                bullet_match = re.match(r"[-*]\s+`?([^`]+?\.[A-Za-z0-9_]+)`?$", stripped)
                if collecting == "targets" and bullet_match:
                    add_unique(target_files, bullet_match.group(1))
                    continue
                if collecting == "conflict_keys" and stripped.startswith(("-", "*")):
                    add_unique(conflict_keys, stripped[1:])
                    continue
                if collecting == "allowed_tools" and stripped.startswith(("-", "*")):
                    add_unique(allowed_tools, stripped[1:])
                    continue
                collecting = ""

            for prefix in ("Target artifacts:", "- allowed_artifacts:", "- target_artifacts:", "Target files:"):
                if stripped.startswith(prefix):
                    raw_targets = stripped.split(":", 1)[1]
                    for value in raw_targets.split(","):
                        add_unique(target_files, value)
            if stripped.startswith("Wave allowed artifacts:"):
                raw_targets = stripped.split(":", 1)[1]
                for value in raw_targets.split(","):
                    add_unique(target_files, value)
            if stripped.startswith("Conflict keys:") or stripped.startswith("- conflict_keys:"):
                raw_keys = stripped.split(":", 1)[1]
                for value in raw_keys.split(","):
                    add_unique(conflict_keys, value)
            if stripped.startswith("Allowed tools:") or stripped.startswith("- allowed_tools:"):
                raw_tools = stripped.split(":", 1)[1]
                for value in raw_tools.split(","):
                    add_unique(allowed_tools, value)

        if not target_files:
            match = re.search(
                r"\b(?:Implement/Fix|Fix|Continue implementation of)\s+([^\s]+\.[A-Za-z0-9_]+)\b",
                sub_task or "",
            )
            if match:
                add_unique(target_files, match.group(1))
        return target_files, conflict_keys, allowed_tools
