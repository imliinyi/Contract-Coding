import time
from typing import Any, Dict, List, Optional, Tuple

from langgraph.graph import END

from MetaFlow.agents.base import BaseAgent
from MetaFlow.config import Config
from MetaFlow.traverser import GraphTraverser
from MetaFlow.memory.audit import audit_file_existence, audit_file_versions
from MetaFlow.memory.document import DocumentManager
from MetaFlow.memory.processor import MemoryProcessor
from MetaFlow.runner import AgentRunner
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState


class Engine:
    """
    The engine for the MetaFlow.
    """
    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents : Dict[str, BaseAgent | None] = {END: None}
        self.start_agent : Optional[str] = None
        self.is_train = True
        self.termination_policy = config.TERMINATION_POLICY

        self.memory_processor = MemoryProcessor(self.config, list(self.agents.keys()), self.config.MEMORY_WINDOW)
        self.document_manager = DocumentManager()
        self.agent_runner = AgentRunner(
            config=self.config,
            agents=self.agents,
            memory_processor=self.memory_processor,
            document_manager=self.document_manager,
        )
        self.graph_traverser: Optional[GraphTraverser] = GraphTraverser(
            config=self.config,
            agent_runner=self.agent_runner,
            memory_processor=self.memory_processor,
            document_manager=self.document_manager
        )

        if self.termination_policy not in ['any', 'majority', 'all']:
            raise ValueError("TERMINATION_PLOICY must be one of ['any', 'majority', 'all'].")

    def _initialize_state(self, input: str) -> GeneralState:
        """
        Initialize the state for the input.
        """
        return GeneralState(
            task=input,
            sub_task="",
            role="user",
            thinking="",
            output="",
            next_agents=[self.start_agent],
            task_requirements={self.start_agent: input}
        )

    def _run_single_step(self, input: str) -> Tuple[GeneralState, bool]:
        """
        Run a single step of the MetaFlow.
        """
        self.document_manager = DocumentManager() # Reset for each run
        initial_state = self._initialize_state(input)
        
        # Forward propagation
        all_layers, execution_trace, terminating_states = self.graph_traverser.traverse(
            self.start_agent, initial_state)

        final_state = terminating_states[0] if terminating_states else None
        is_success = final_state is not None

        return final_state, is_success

    def _persist_execution_trace(self, execution_trace: List[Tuple[str, str]], input_task: str) -> None:
        """
        Append current run's execution edges to a JSONL file for later mining.
        Only stores edges (u, v), without rewards.
        """
        import os
        import json
        from datetime import datetime

        # Prepare edges-only list
        edges = [[u, v] for (u, v) in execution_trace]
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "task": input_task,
            "edges": edges
        }

        # Ensure history directory exists
        os.makedirs("history", exist_ok=True)
        trace_file = os.path.join("history", "exec_traces.jsonl")

        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        
    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name

    def train(self, inputs: List[str]) -> List[Dict[str, Any]]:
        """
        Train the MetaFlow on the given inputs.
        """
        self.is_train = True

        results = []
        self.logger.info(f"--- Training on {len(inputs)} samples ---")
        for i, input_task in enumerate(inputs):
            final_state, is_success = self._run_single_step(input_task)

            try:
                # Recompute trace cheaply for persistence (edges only)
                _, execution_trace, _ = self.graph_traverser.traverse(
                    self.start_agent, self._initialize_state(input_task))
                self._persist_execution_trace(execution_trace, input_task=input_task)
            except Exception as e:
                self.logger.error(f"Persist trace during train failed: {e}")

            time.sleep(10) # Avoid too many requests to the server
            results.append({
                'input_task': input_task,
                'final_state': final_state,
                'is_success': is_success,
            })
            self.logger.info(f"--- Sample {i+1}/{len(inputs)} - Success: {is_success} ---")

        self.logger.info(f"--- Training Finished ---")
        return results

    def run(self, input_task: str) -> str:
        """
        Run the MetaFlow on the given input task.
        """
        self.is_train = False
        final_state, is_success = self._run_single_step(input_task)

        # Audit file existence and versions
        if self.document_manager:
            print("\n--- Running Audits ---")
            with open('document.md', 'r', encoding='utf-8') as f:
                document_content = f.read()
            audit_file_existence(document_content, 'workspace')
            audit_file_versions(document_content, 'workspace')  
            print("--- Audits Complete ---\n")

        return final_state

    def find_all_paths(self, graph: Dict[str, List[str]], start: str, end: str) -> List[List[str]]:
        """
        Find all paths from start_agent to END in the graph.
        """
        def dfs(current: str, path: List[str], paths: List[List[str]]):
            path.append(current)
            if current == end:
                paths.append(path.copy())
            elif current in graph:
                for neighbor in graph.get(current, []):
                    dfs(neighbor, path, paths)
            path.pop()

        all_paths = []
        dfs(start, [], all_paths)
        return all_paths

    