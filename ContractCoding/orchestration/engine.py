import time
from typing import Any, Dict, List, Optional, Tuple

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.memory.audit import audit_file_existence, audit_file_versions
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.constants import END
from ContractCoding.orchestration.runner import AgentRunner
from ContractCoding.orchestration.traverser import GraphTraverser
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


class Engine:
    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents: Dict[str, BaseAgent | None] = {END: None}
        self.start_agent: Optional[str] = None
        self.is_train = True
        self.termination_policy = config.TERMINATION_POLICY

        self.memory_processor: MemoryProcessor | None = None
        self.document_manager: DocumentManager | None = None
        self.agent_runner: AgentRunner | None = None
        self.graph_traverser: Optional[GraphTraverser] = None
        self._reset_runtime_state()

        if self.termination_policy not in ['any', 'majority', 'all']:
            raise ValueError("TERMINATION_PLOICY must be one of ['any', 'majority', 'all'].")

    def _reset_runtime_state(self) -> None:
        self.memory_processor = MemoryProcessor(self.config, list(self.agents.keys()), self.config.MEMORY_WINDOW)
        self.document_manager = DocumentManager(workspace_dir=self.config.WORKSPACE_DIR)
        self.agent_runner = AgentRunner(
            config=self.config,
            agents=self.agents,
            memory_processor=self.memory_processor,
            document_manager=self.document_manager,
        )
        self.graph_traverser = GraphTraverser(
            config=self.config,
            agent_runner=self.agent_runner,
            memory_processor=self.memory_processor,
            document_manager=self.document_manager,
        )

    def _initialize_state(self, input: str) -> GeneralState:
        return GeneralState(
            task=input,
            sub_task="",
            role="user",
            thinking="",
            output="",
            next_agents=[self.start_agent],
            task_requirements={self.start_agent: input},
        )

    def _run_single_step(self, input: str) -> Tuple[GeneralState | None, bool]:
        self._reset_runtime_state()
        initial_state = self._initialize_state(input)

        _, execution_trace, terminating_states = self.graph_traverser.traverse(self.start_agent, initial_state)

        final_state = terminating_states[0] if terminating_states else None
        is_success = final_state is not None

        return final_state, is_success

    def _persist_execution_trace(self, execution_trace: List[Tuple[str, str]], input_task: str) -> None:
        import os
        import json
        from datetime import datetime

        edges = [[u, v] for (u, v) in execution_trace]
        record = {"timestamp": datetime.utcnow().isoformat(), "task": input_task, "edges": edges}

        os.makedirs("history", exist_ok=True)
        trace_file = os.path.join("history", "exec_traces.jsonl")

        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name
        if self.memory_processor is not None:
            self.memory_processor.agents = list(self.agents.keys())

    def train(self, inputs: List[str]) -> List[Dict[str, Any]]:
        self.is_train = True
        results = []
        self.logger.info(f"--- Training on {len(inputs)} samples ---")
        for i, input_task in enumerate(inputs):
            final_state, is_success = self._run_single_step(input_task)

            try:
                _, execution_trace, _ = self.graph_traverser.traverse(self.start_agent, self._initialize_state(input_task))
                self._persist_execution_trace(execution_trace, input_task=input_task)
            except Exception as e:
                self.logger.error(f"Persist trace during train failed: {e}")

            time.sleep(10)
            results.append({"input_task": input_task, "final_state": final_state, "is_success": is_success})
            self.logger.info(f"--- Sample {i+1}/{len(inputs)} - Success: {is_success} ---")

        self.logger.info("--- Training Finished ---")
        return results

    def run(self, input_task: str) -> GeneralState | None:
        self.is_train = False
        final_state, _ = self._run_single_step(input_task)

        if self.document_manager:
            try:
                import io
                import contextlib

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    document_content = self.document_manager.get()
                    audit_file_existence(document_content, self.config.WORKSPACE_DIR)
                    audit_file_versions(document_content, self.config.WORKSPACE_DIR)
                audit_output = buf.getvalue().strip()
                if audit_output:
                    self.logger.info(f"--- Audits ---\n{audit_output}\n--- Audits End ---")
            except Exception as e:
                self.logger.error(f"Audit failed: {e}")

        return final_state
