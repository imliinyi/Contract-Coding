from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

from ContractCoding.config import Config
from ContractCoding.memory.audit import AuditIssue, audit_contract_interfaces, check_missing_specs
from ContractCoding.memory.contract import ContractFile, ContractKernel, ContractParseError
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.runner import AgentRunner
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState


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
        self.last_audit_issues: list[AuditIssue] = []
        self.last_blocked_tasks: dict[str, list[str]] = {}

    def traverse(
        self, start_agent: str, initial_states: Any
    ) -> Tuple[List[Dict[str, List[GeneralState]]], List[Tuple[str, str]], List[GeneralState]]:
        execution_trace: List[Tuple[str, str]] = []
        initial_state_list = initial_states if isinstance(initial_states, list) else [initial_states]
        all_layers: List[Dict[str, List[GeneralState]]] = [{start_agent: initial_state_list}]
        terminating_states: List[GeneralState] = []
        executed_agents_history = []

        while all_layers[-1] and len(all_layers) <= self.config.MAX_LAYERS:
            current_level_agents = all_layers[-1]
            layer_index = len(all_layers) - 1

            base_version = self.document_manager.get_version()
            try:
                self.document_manager.begin_layer_aggregation(base_version)
            except Exception:
                pass

            layer_executed_agents = []

            def _run_single(agent_name: str, state: GeneralState) -> None:
                next_available_agents = self.agent_runner.agents
                print(f"--- Current Agent: {agent_name} ---")
                self.logger.info(f"--- Current Agent: {agent_name} | Sub Task: {state.sub_task} ---")
                output_state = self.agent_runner.run(
                    agent_name=agent_name,
                    state=state,
                    next_available_agents=next_available_agents,
                )
                self.memory_processor.add_message(agent_name, output_state)
                execution_trace.append((agent_name, "NEXT_LAYER"))

            max_workers = getattr(self.config, "MAX_WORKERS", 16)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for agent_name, states in current_level_agents.items():
                    for state in states:
                        futures[executor.submit(_run_single, agent_name, state)] = agent_name

                for future in as_completed(futures):
                    agent_name = futures[future]
                    layer_executed_agents.append(agent_name)
                    try:
                        future.result()
                    except Exception as e:
                        self.logger.error(f"Agent {agent_name} failed with error: {e}")

            try:
                self.document_manager.commit_layer_aggregation()
            except Exception as e:
                self.logger.error(f"Document layer aggregation commit failed: {e}")

            executed_agents_history.append(set(layer_executed_agents))
            doc_content = self.document_manager.get()

            if layer_index == 0:
                next_level_agents_map = self._schedule_architect_or_contract_repair(doc_content, current_level_agents)
            else:
                next_level_agents_map = self._schedule_from_contract(doc_content, current_level_agents)

            if not next_level_agents_map:
                pending_tasks = self._pending_tasks(doc_content)
                if not pending_tasks:
                    for states in current_level_agents.values():
                        terminating_states.extend(states)
                    break

                pm_msg = (
                    "Critical: The workflow has stalled. There are pending tasks but no agents were scheduled. "
                    "Review the structured Contract Kernel, dependencies, owners, statuses, and interface audit issues.\n\n"
                    f"Failure report:\n{self.build_failure_report(doc_content)}"
                )
                base_state = list(current_level_agents.values())[0][0]
                new_state = base_state.model_copy()
                new_state.sub_task = pm_msg
                next_level_agents_map["Project_Manager"].append(new_state)

            next_layer_plan = {k: len(v) for k, v in next_level_agents_map.items()}
            print(f"--- Next Layer Agents: {next_layer_plan} ---")
            self.logger.info(f"--- Next Layer Plan: {next_layer_plan} ---")
            all_layers.append(next_level_agents_map)

        if len(all_layers) > self.config.MAX_LAYERS:
            self.logger.error("Maximum orchestration layers reached before all tasks were VERIFIED.")

        return all_layers, execution_trace, terminating_states

    def _schedule_architect_or_contract_repair(
        self, doc_content: str, current_layer_agents: Dict[str, List[GeneralState]]
    ) -> Dict[str, List[GeneralState]]:
        next_level_agents_map = defaultdict(list)
        base_state = list(current_layer_agents.values())[0][0]
        is_valid, validation_errors = self._validate_project_structure(doc_content)

        if not is_valid:
            self.logger.warning(f"--- Document Validation Failed: {validation_errors} ---")
            pm_msg = (
                "Critical: The Collaborative Document cannot be converted into a structured Contract Kernel. "
                "Fix the document using `document_action`. Required fields include **File:**, **Owner:**, "
                "**Version:**, and **Status:** for every file block.\n\n"
                f"Validation errors:\n" + "\n".join(f"- {e}" for e in validation_errors)
            )
            new_state = base_state.model_copy()
            new_state.sub_task = pm_msg
            next_level_agents_map["Project_Manager"].append(new_state)
            return next_level_agents_map

        task_msg = (
            "Contract review ONLY (no implementation review). Review and repair the Project Plan and Architecture generated by Project Manager. "
            "Ensure contracts are clear, complete, internally consistent, and describe end-to-end runnable flows (including game loop, systems, and UI wiring). "
            "Do NOT set any file Status to VERIFIED or DONE during this phase. "
            "If the contract has blocking issues, set the affected file blocks to ERROR and append actionable issue bullets after the Status line."
        )
        new_state = base_state.model_copy()
        new_state.sub_task = task_msg
        next_level_agents_map["Architect"].append(new_state)
        return next_level_agents_map

    def _validate_project_structure(self, content: str) -> Tuple[bool, List[str]]:
        validation_errors = []

        missing_specs = check_missing_specs(content)
        validation_errors.extend(
            f"File defined in Directory Structure but missing from Symbolic API Specifications: {f}"
            for f in missing_specs
        )

        try:
            kernel = self.document_manager.get_kernel()
        except ContractParseError as exc:
            validation_errors.extend(issue.format() for issue in exc.issues)
            return False, validation_errors
        except Exception as exc:
            validation_errors.append(f"Contract Kernel parse failed: {exc}")
            return False, validation_errors

        if not kernel.files:
            validation_errors.append("Contract Kernel contains no file tasks under Symbolic API Specifications.")

        return (not validation_errors), validation_errors

    def _pending_tasks(self, doc_content: str) -> list[ContractFile]:
        try:
            return [task for task in self.document_manager.get_kernel().files if task.status != 'VERIFIED']
        except Exception:
            return []

    def _parse_contract(self, content: str) -> List[Dict[str, str]]:
        try:
            kernel = self.document_manager.get_kernel()
        except Exception:
            return []
        return [
            {
                'file': task.path,
                'owner': task.owner,
                'status': task.status,
                'block': task.block,
            }
            for task in kernel.files
        ]

    def _extract_issues_from_task(self, task: ContractFile) -> str:
        lines = task.block.split('\n')
        for i, line in enumerate(lines):
            if '**Status' in line:
                tail = [ln.rstrip() for ln in lines[i + 1 :] if ln.strip()]
                return "\n".join(tail).strip()
        return ""

    def _extract_contract_description_from_task(self, task: ContractFile, max_lines: int = 30) -> str:
        out: list[str] = []
        for raw_ln in task.block.split('\n'):
            ln = raw_ln.rstrip()
            if not ln.strip() or ln.strip().startswith('**File:**'):
                continue
            if any(marker in ln for marker in ('**Owner', '**Version', '**Status')):
                continue
            out.append(ln)
            if len(out) >= max_lines:
                break
        return "\n".join(out).strip()

    def _schedule_from_contract(
        self, doc_content: str, current_layer_agents: Dict[str, List[GeneralState]]
    ) -> Dict[str, List[GeneralState]]:
        next_agents_map = defaultdict(list)
        base_state = list(current_layer_agents.values())[0][0]

        try:
            kernel = self.document_manager.get_kernel()
        except ContractParseError as exc:
            pm_msg = (
                "Critical: The Collaborative Document cannot be parsed into the structured Contract Kernel. "
                "Fix these contract fields before implementation continues:\n"
                + "\n".join(f"- {issue.format()}" for issue in exc.issues)
            )
            new_state = base_state.model_copy()
            new_state.sub_task = pm_msg
            next_agents_map["Project_Manager"].append(new_state)
            return next_agents_map

        if not kernel.files:
            pm_msg = "Critical: The Contract Kernel has no file tasks. Add concrete **File:** blocks with Owner, Version, and Status."
            new_state = base_state.model_copy()
            new_state.sub_task = pm_msg
            next_agents_map["Project_Manager"].append(new_state)
            return next_agents_map

        missing_specs = check_missing_specs(doc_content)
        if missing_specs:
            pm_msg = (
                "Critical: Directory Structure and Symbolic API Specifications disagree. "
                f"Add detailed file blocks for: {missing_specs}."
            )
            new_state = base_state.model_copy()
            new_state.sub_task = pm_msg
            next_agents_map["Project_Manager"].append(new_state)
            return next_agents_map

        audit_issues = audit_contract_interfaces(kernel, self.config.WORKSPACE_DIR)
        self.last_audit_issues = audit_issues
        file_by_path = kernel.by_path()
        done_tasks: list[ContractFile] = []
        blocked_tasks: dict[str, list[str]] = {}

        for task in kernel.files:
            if task.status == 'VERIFIED':
                continue
            if task.status == 'DONE':
                done_tasks.append(task)
                continue
            if task.status not in {'TODO', 'IN_PROGRESS', 'ERROR'}:
                continue

            unmet_dependencies = [
                dep for dep in kernel.dependencies.get(task.path, [])
                if dep in file_by_path and file_by_path[dep].status != 'VERIFIED'
            ]
            unknown_dependencies = [dep for dep in kernel.dependencies.get(task.path, []) if dep not in file_by_path]
            if unmet_dependencies or unknown_dependencies:
                blocked_tasks[task.path] = unmet_dependencies + [f"unknown:{dep}" for dep in unknown_dependencies]
                continue

            issue_text = self._extract_issues_from_task(task) if task.status == 'ERROR' else ""
            contract_desc = self._extract_contract_description_from_task(task)
            msg = self._build_worker_message(task, contract_desc, issue_text)
            new_state = base_state.model_copy()
            new_state.sub_task = msg
            next_agents_map[task.owner].append(new_state)

        if done_tasks:
            review_msg = self._build_review_message(done_tasks, audit_issues)
            critic_state = base_state.model_copy()
            critic_state.sub_task = review_msg
            next_agents_map["Critic"].append(critic_state)

            reviewer_state = base_state.model_copy()
            reviewer_state.sub_task = review_msg
            next_agents_map["Code_Reviewer"].append(reviewer_state)

        self.last_blocked_tasks = blocked_tasks
        if blocked_tasks and not next_agents_map:
            pm_msg = (
                "Critical: All remaining implementation tasks are blocked by unmet or unknown dependencies. "
                "Review the Dependency Relationships section and update the Contract if the dependency graph is wrong.\n\n"
                + self._format_blocked_tasks(blocked_tasks)
            )
            new_state = base_state.model_copy()
            new_state.sub_task = pm_msg
            next_agents_map["Project_Manager"].append(new_state)

        return next_agents_map

    def _build_worker_message(self, task: ContractFile, contract_desc: str, issue_text: str) -> str:
        if issue_text:
            return (
                f"Fix {task.path}. Current Status: {task.status}. Follow the structured Contract Kernel strictly.\n\n"
                "File contract summary (from Symbolic API Specifications):\n"
                f"{contract_desc}\n\n"
                "Contract issues to resolve (from the file block):\n"
                f"{issue_text}\n\n"
                "After fixes, update the same file block status to DONE via <document_action>."
            )
        return (
            f"Implement/Fix {task.path}. Current Status: {task.status}. Follow the structured Contract Kernel strictly.\n\n"
            "File contract summary (from Symbolic API Specifications):\n"
            f"{contract_desc}\n\n"
            "After implementation, update the same file block status to DONE via <document_action>."
        )

    def _build_review_message(self, done_tasks: list[ContractFile], audit_issues: list[AuditIssue]) -> str:
        files = [task.path for task in done_tasks]
        file_set = set(files)
        relevant_issues = [issue for issue in audit_issues if issue.path in file_set]
        file_list = "\n".join(f"- {path}" for path in files)
        issue_list = "\n".join(f"- {issue.format()}" for issue in relevant_issues) or "- No structured interface audit issues found."
        return (
            "Batch review the following completed tasks (Status: DONE). "
            "For EACH file, read the implementation, compare against the Contract Kernel and Markdown contract, and update the document status. "
            "If correct: set Status to VERIFIED. If incorrect: set Status to ERROR and delegate fixes with clear instructions. "
            "You MUST use the `document_action` tool to update statuses (do not only describe in text).\n\n"
            "Files to review:\n"
            f"{file_list}\n\n"
            "Structured interface audit findings:\n"
            f"{issue_list}"
        )

    def _format_blocked_tasks(self, blocked_tasks: dict[str, list[str]]) -> str:
        lines = ["Blocked tasks:"]
        for path, deps in sorted(blocked_tasks.items()):
            lines.append(f"- {path}: waiting for {', '.join(deps)}")
        return "\n".join(lines)

    def build_failure_report(self, doc_content: str | None = None) -> str:
        try:
            kernel: ContractKernel = self.document_manager.get_kernel()
        except ContractParseError as exc:
            return "Contract Kernel parse failed:\n" + "\n".join(f"- {issue.format()}" for issue in exc.issues)
        except Exception as exc:
            return f"Contract Kernel unavailable: {exc}"

        unfinished = [task for task in kernel.files if task.status != 'VERIFIED']
        if not unfinished:
            return "All contract tasks are VERIFIED."

        issue_by_path: dict[str, list[AuditIssue]] = defaultdict(list)
        for issue in self.last_audit_issues:
            issue_by_path[issue.path].append(issue)

        lines = ["Unfinished contract tasks:"]
        for task in unfinished:
            reasons: list[str] = []
            if task.path in self.last_blocked_tasks:
                reasons.append("dependency_blocked=" + ", ".join(self.last_blocked_tasks[task.path]))
            if task.path in issue_by_path:
                reasons.extend(issue.format() for issue in issue_by_path[task.path])
            if not reasons:
                reasons.append("pending_or_agent_incomplete")
            lines.append(f"- {task.path}: status={task.status}; owner={task.owner}; reason={' | '.join(reasons)}")
        if len(unfinished) > 0:
            lines.append(f"Max layers configured: {self.config.MAX_LAYERS}")
        return "\n".join(lines)


Orchestrator = GraphTraverser
