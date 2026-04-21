from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.constants import END
from ContractCoding.orchestration.runner import AgentRunner
from ContractCoding.orchestration.traverser import GraphTraverser
from ContractCoding.review.audits import ContractAuditRunner
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState

if TYPE_CHECKING:
    from ContractCoding.agents.forge import AgentForge


@dataclass
class RuntimeSession:
    memory_processor: MemoryProcessor
    document_manager: DocumentManager
    agent_runner: AgentRunner
    graph_traverser: GraphTraverser


class ContractCodingService:
    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents: Dict[str, BaseAgent | None] = {END: None}
        self.start_agent: Optional[str] = None
        self.is_train = True
        self.termination_policy = config.TERMINATION_POLICY
        self.audit_runner = ContractAuditRunner(config.WORKSPACE_DIR)
        self.runtime = self._build_runtime_session()

        if self.termination_policy not in ["any", "majority", "all"]:
            raise ValueError("TERMINATION_PLOICY must be one of ['any', 'majority', 'all'].")

    @property
    def memory_processor(self) -> MemoryProcessor:
        return self.runtime.memory_processor

    @property
    def document_manager(self) -> DocumentManager:
        return self.runtime.document_manager

    @property
    def agent_runner(self) -> AgentRunner:
        return self.runtime.agent_runner

    @property
    def graph_traverser(self) -> GraphTraverser:
        return self.runtime.graph_traverser

    def _build_runtime_session(self) -> RuntimeSession:
        memory_processor = MemoryProcessor(self.config, list(self.agents.keys()), self.config.MEMORY_WINDOW)
        document_manager = DocumentManager(workspace_dir=self.config.WORKSPACE_DIR)
        agent_runner = AgentRunner(
            config=self.config,
            agents=self.agents,
            memory_processor=memory_processor,
            document_manager=document_manager,
        )
        graph_traverser = GraphTraverser(
            config=self.config,
            agent_runner=agent_runner,
            memory_processor=memory_processor,
            document_manager=document_manager,
        )
        return RuntimeSession(
            memory_processor=memory_processor,
            document_manager=document_manager,
            agent_runner=agent_runner,
            graph_traverser=graph_traverser,
        )

    def _reset_runtime_state(self) -> None:
        self.runtime = self._build_runtime_session()

    def _initialize_state(self, input_task: str) -> GeneralState:
        return GeneralState(
            task=input_task,
            sub_task="",
            role="user",
            thinking="",
            output="",
            next_agents=[self.start_agent],
            task_requirements={self.start_agent: input_task},
        )

    def _run_single_step(self, input_task: str) -> Tuple[GeneralState | None, List[Tuple[str, str]]]:
        self._reset_runtime_state()
        initial_state = self._initialize_state(input_task)
        _, execution_trace, terminating_states = self.graph_traverser.traverse(self.start_agent, initial_state)
        final_state = terminating_states[0] if terminating_states else None
        return final_state, execution_trace

    def _persist_execution_trace(self, execution_trace: List[Tuple[str, str]], input_task: str) -> None:
        os.makedirs("history", exist_ok=True)
        trace_file = os.path.join("history", "exec_traces.jsonl")
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "task": input_task,
            "edges": [[u, v] for (u, v) in execution_trace],
        }
        with open(trace_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name
        self.memory_processor.agents = list(self.agents.keys())

    def register_default_agents(self, forge: "AgentForge" | None = None) -> None:
        if forge is None:
            from ContractCoding.agents.forge import AgentForge

            forge = AgentForge(self.config)
        agent_forge = forge
        for agent_name, agent in agent_forge.create_default_agents().items():
            self.register_agent(agent_name, agent, is_start=(agent_name == "Project_Manager"))

    def train(self, inputs: List[str]) -> List[Dict[str, Any]]:
        self.is_train = True
        results = []
        self.logger.info("--- Training on %s samples ---", len(inputs))
        for index, input_task in enumerate(inputs):
            final_state, execution_trace = self._run_single_step(input_task)
            self._persist_execution_trace(execution_trace, input_task=input_task)
            time.sleep(10)
            is_success = final_state is not None
            results.append({"input_task": input_task, "final_state": final_state, "is_success": is_success})
            self.logger.info("--- Sample %s/%s - Success: %s ---", index + 1, len(inputs), is_success)
        self.logger.info("--- Training Finished ---")
        return results

    def run(self, input_task: str) -> GeneralState | None:
        self.is_train = False
        final_state, _ = self._run_single_step(input_task)
        audit_output = self.audit_runner.run(self.document_manager.get())
        if audit_output:
            self.logger.info("--- Audits ---\n%s\n--- Audits End ---", audit_output)
        return final_state
