from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

from langgraph.graph import END

from MetaFlow.config import Config
from MetaFlow.memory.audit import check_missing_files, check_missing_specs
from MetaFlow.memory.document import DocumentManager
from MetaFlow.memory.processor import MemoryProcessor
from MetaFlow.runner import AgentRunner
from MetaFlow.utils.log import get_logger
from MetaFlow.utils.state import GeneralState

class GraphTraverser:
    def __init__(
        self,
        config: Config,
        agent_runner: AgentRunner,
        memory_processor: MemoryProcessor,
        document_manager: DocumentManager,
    ):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agent_runner = agent_runner
        self.memory_processor = memory_processor
        self.termination_policy = self.config.TERMINATION_POLICY
        self.document_manager = document_manager

    def traverse(
        self, start_agent: str, initial_states: Dict[str, GeneralState]
    ) -> Tuple[List[Dict[str, GeneralState]], List[Tuple[str, str, float]], List[GeneralState]]:
        """
        Forward propagation through the layers of the graph.
        """
        execution_trace: List[Dict[str, str, float]] = []
        all_layers: List[Dict[str, GeneralState]] = [{start_agent: initial_states}]
        terminating_states: List[GeneralState] = []

        while all_layers[-1] and len(all_layers) <= self.config.MAX_LAYERS:
            current_level_agents = all_layers[-1]
            layer_index = len(all_layers) - 1

            layer_outputs = []
            next_level_agents = defaultdict(list)

            # Begin per-layer document aggregation using a consistent base snapshot
            base_version = self.document_manager.get_version()
            try:
                self.document_manager.begin_layer_aggregation(base_version)
            except Exception:
                pass

            with ThreadPoolExecutor() as executor:
                # The logic for getting next_available_agents is the same for all agents in the layer
                next_available_agents = self.agent_runner.agents

                futures = {
                    executor.submit(
                        self.agent_runner.run,
                        agent_name=agent_name,
                        state=state,
                        next_available_agents=next_available_agents
                    ): agent_name
                    for agent_name, state in current_level_agents.items()
                }

                for future in as_completed(futures):
                    agent_name = futures[future]
                    try:
                        print(f"\n--- Current Agent: {agent_name} ---")
                        output_state = future.result()

                        # Add the output state to memory
                        self.memory_processor.add_message(agent_name, output_state)

                        next_agents = output_state.next_agents
                        continuing_agents, is_terminating = self._parse_agent_output(next_agents)

                        layer_outputs.append({
                            'agent_name': agent_name,
                            'next_agents': next_agents,
                            'continuing_agents': continuing_agents,
                            'is_terminating': is_terminating,
                            'output_state': output_state,
                        })
                    except Exception as e:
                        self.logger.error(f"Agent {agent_name} failed with error: {e}")

            # Commit per-layer aggregated document updates before computing rewards/next agents
            try:
                self.document_manager.commit_layer_aggregation()
            except Exception as e:
                self.logger.error(f"Document layer aggregation commit failed: {e}")

            # --- Mechanism 2 (Higher Priority): Spec Coverage Gate (2.1 vs 2.4) ---
            doc_content = self.document_manager.get()
            workspace_path = self.config.WORKSPACE_DIR
            missing_specs = check_missing_specs(doc_content) if self.config.SPEC_GATING_ENABLED else []

            if self.config.SPEC_GATING_ENABLED and missing_specs:
                print(f"\n[Audit] Missing 2.4 specs for files listed in 2.1: {missing_specs}. Forcing Project_Manager only.")
                for output in layer_outputs:
                    # Override next and continuing agents: enforce spec-first gating
                    output['next_agents'] = ["Project_Manager"]
                    output['continuing_agents'] = ["Project_Manager"]
                    output['is_terminating'] = False

                    task_msg = (
                        f"CRITICAL: The following files are listed under Directory Structure (2.1) but lack `Symbolic API Specifications` in 2.4: {missing_specs}. "
                        f"Update the Collaborative Document to add complete 2.4 specs (File blocks) for these files before continuing."
                    )
                    if output['output_state'].task_requirements is None:
                        output['output_state'].task_requirements = {}
                    output['output_state'].task_requirements["Project_Manager"] = task_msg
            else:
                # --- Mechanism 1 (Lower Priority): Existence Check (Specs reference files missing in workspace)
                missing = check_missing_files(doc_content, workspace_path)
                if missing:
                    print(f"\n[Audit] Files defined in 2.4 but missing in workspace: {missing}. Adding Project_Manager to next layer.")
                    for output in layer_outputs:
                        # Append Project_Manager instead of overriding
                        if output['next_agents'] is None:
                            output['next_agents'] = []
                        if "Project_Manager" not in output['next_agents']:
                            output['next_agents'].append("Project_Manager")

                        if "Project_Manager" not in output['continuing_agents']:
                            output['continuing_agents'].append("Project_Manager")

                        output['is_terminating'] = False

                        task_msg = (
                            f"CRITICAL: The following files are defined in the Collaborative Document (2.4) but are missing in the workspace: {missing}. "
                            f"Create these files and align implementations before resuming normal execution."
                        )
                        if output['output_state'].task_requirements is None:
                            output['output_state'].task_requirements = {}
                        output['output_state'].task_requirements["Project_Manager"] = task_msg

            num_terminating = sum([1 for o in layer_outputs if o['is_terminating']])
            num_total = len(layer_outputs)

            learn_terminating_only = False
            if self.termination_policy == 'any' and num_terminating > 0:
                learn_terminating_only = True
            elif self.termination_policy == 'majority' and num_total > 0 and num_terminating > num_total / 2:
                learn_terminating_only = True

            for output in layer_outputs:
                agent_name = output['agent_name']
                next_agents = output['next_agents'] or []
                continuing_agents = output['continuing_agents']
                output_state = output['output_state']

                for next_agent in next_agents:
                    execution_trace.append((agent_name, next_agent))

                    if next_agent == END:
                        terminating_states.append(output_state)

                for cont_n in continuing_agents:
                    # Create a new state for each downstream agent with its specific sub-task
                    task_reqs = output_state.task_requirements
                    sub_task = task_reqs.get(cont_n, output_state.sub_task)
                    new_state_for_next_agent = output_state.model_copy()
                    new_state_for_next_agent.sub_task = sub_task
                    next_level_agents[cont_n].append(new_state_for_next_agent)

            if learn_terminating_only and self.termination_policy != 'all':
                break

            next_level_agents = {
                agent_name: self.memory_processor.merge_memory(states)
                for agent_name, states in next_level_agents.items()
            }
            if not next_level_agents:
                print("--- Next Layer Agents: [] ---")
                break
            print(f"--- Next Layer Agents: {list(next_level_agents.keys())} ---")
            all_layers.append(next_level_agents)

        return all_layers, execution_trace, terminating_states

    def _parse_agent_output(self, next_agents: Any) -> Tuple[List[str], bool]:
        """
        Parse the output of an agent to determine the next agents to continue with.
        Returns a tuple of (next_agents, is_terminating).
        """
        if not next_agents or next_agents == END:
            return [], True
        if isinstance(next_agents, list):
            # Remove END from the list of next agents
            continuing_agents = [n for n in next_agents if n != END]
            is_terminating = len(continuing_agents) < len(next_agents)
            return continuing_agents, is_terminating
        # If the output is not a list, wrap it in a list
        return [next_agents], False
