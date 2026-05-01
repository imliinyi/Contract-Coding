from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ContractCoding.agents.base import BaseAgent
from ContractCoding.config import Config
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.contract.spec import ContractSpec
from ContractCoding.constants import END
from ContractCoding.execution.runner import AgentRunner
from ContractCoding.runtime.engine import AutoRunResult, RunEngine
from ContractCoding.utils.log import get_logger
from ContractCoding.utils.state import GeneralState

if TYPE_CHECKING:
    from ContractCoding.agents.forge import AgentForge


@dataclass
class RuntimeSession:
    context_manager: ContextManager
    agent_runner: AgentRunner
    run_engine: RunEngine


class ContractCodingService:
    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.agents: Dict[str, BaseAgent | None] = {END: None}
        self.start_agent: Optional[str] = None
        self.is_train = True
        self.termination_policy = config.TERMINATION_POLICY
        self.runtime = self._build_runtime_session()

        if self.termination_policy not in ["any", "majority", "all"]:
            raise ValueError("TERMINATION_PLOICY must be one of ['any', 'majority', 'all'].")

    @property
    def context_manager(self) -> ContextManager:
        return self.runtime.context_manager

    @property
    def agent_runner(self) -> AgentRunner:
        return self.runtime.agent_runner

    @property
    def run_engine(self) -> RunEngine:
        return self.runtime.run_engine

    def _build_runtime_session(self) -> RuntimeSession:
        context_manager = ContextManager(self.config, list(self.agents.keys()), self.config.MEMORY_WINDOW)
        agent_runner = AgentRunner(
            config=self.config,
            agents=self.agents,
            context_manager=context_manager,
        )
        run_engine = RunEngine(
            config=self.config,
            agent_runner=agent_runner,
            context_manager=context_manager,
        )
        return RuntimeSession(
            context_manager=context_manager,
            agent_runner=agent_runner,
            run_engine=run_engine,
        )

    def _reset_runtime_state(self) -> None:
        self.runtime = self._build_runtime_session()

    def register_agent(self, agent_name: str, agent: BaseAgent, is_start: bool = False) -> None:
        self.agents[agent_name] = agent
        if is_start:
            self.start_agent = agent_name
        self.context_manager.agents = list(self.agents.keys())

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
            final_state = self.run(input_task)
            time.sleep(10)
            is_success = final_state is not None
            results.append({"input_task": input_task, "final_state": final_state, "is_success": is_success})
            self.logger.info("--- Sample %s/%s - Success: %s ---", index + 1, len(inputs), is_success)
        self.logger.info("--- Training Finished ---")
        return results

    def run(self, input_task: str) -> GeneralState | None:
        self.is_train = False
        result = self.run_auto(input_task)
        return GeneralState(
            task=input_task,
            sub_task="",
            role="RunEngineV4",
            thinking=f"Run {result.run_id} completed with status {result.status}.",
            output=f"Task ID: {result.task_id}\nRun ID: {result.run_id}\nStatus: {result.status}\n{result.report}",
            next_agents=[],
        )

    def run_auto(
        self,
        input_task: str,
        max_steps: int | None = None,
        contract_path: str | None = None,
    ) -> AutoRunResult:
        return self.run_engine.run_auto(
            input_task,
            contract_path=contract_path,
            max_steps=max_steps,
        )

    def plan_contract(
        self,
        input_task: str,
        draft: ContractSpec | Dict[str, Any] | None = None,
        write_files: bool = True,
    ) -> ContractSpec:
        return self.run_engine.plan(input_task, draft=draft, write_files=write_files)

    def start_run(
        self,
        input_task: str,
        run_immediately: bool = False,
        max_steps: int | None = None,
        contract_path: str | None = None,
    ) -> str:
        return self.run_engine.start(
            input_task,
            contract_path=contract_path,
            run_immediately=run_immediately,
            max_steps=max_steps,
        )

    def resume_run(self, run_id: str, max_steps: int | None = None):
        return self.run_engine.resume(run_id, max_steps=max_steps)

    def resume_run_auto(self, run_id: str, max_steps: int | None = None) -> AutoRunResult:
        return self.run_engine.resume_auto(run_id, max_steps=max_steps)

    def replan_run(self, run_id: str, feedback: str):
        return self.run_engine.replan(run_id, feedback)

    def cancel_run(self, run_id: str) -> None:
        self.run_engine.cancel(run_id)

    def run_status(self, run_id: str):
        return self.run_engine.status(run_id)

    def run_status_text(self, task_or_run_id: str) -> str:
        status = self.run_engine.status(task_or_run_id)
        run = status["run"]
        task = status.get("task")
        lines = []
        if task:
            lines.append(f"Task {task.id}: {task.status_summary.get('status', run.status)}")
            lines.append(f"Prompt: {task.prompt}")
        lines.append(f"Run {run.id}: {run.status}")
        lines.append(self.run_engine.report(run.id, max_lines=10))
        teams = status.get("scope_teams", [])
        if teams:
            preview = ", ".join(f"{team.scope_id}:{team.status}" for team in teams[:8])
            if len(teams) > 8:
                preview += f", +{len(teams) - 8} more"
            lines.append(f"Teams: {preview}")
        gates = [
            item
            for item in status.get("work_items", [])
            if item.kind == "eval" and item.verification_policy.get("system_gate")
        ]
        if gates:
            lines.append("Gates: " + ", ".join(f"{item.id}:{item.status}" for item in gates[:8]))
        repair_tickets = [
            ticket
            for ticket in status.get("repair_tickets", [])
            if ticket.status in {"OPEN", "RUNNING", "BLOCKED"}
        ]
        if repair_tickets:
            lines.append("Repair tickets:")
            for ticket in repair_tickets[:5]:
                owner = ticket.owner_scope or ticket.source_gate or ticket.source_item_id
                lines.append(
                    f"- {ticket.lane}:{owner}:{ticket.status} "
                    f"attempts={ticket.attempt_count} {ticket.failure_summary[:140]}"
                )
        if status.get("blocked"):
            lines.append("Blocked:")
            for blocked in status["blocked"][:5]:
                lines.append(f"- {blocked.work_item_id}: {blocked.reason}")
        return "\n".join(lines)

    def run_graph(self, run_id: str):
        return self.run_engine.graph(run_id)

    def run_monitor(self, run_id: str, write_file: bool = True):
        return self.run_engine.monitor(run_id, write_file=write_file)

    def run_teams(self, run_id: str):
        return self.run_engine.teams(run_id)

    def run_events(self, run_id: str, limit: int = 50):
        return self.run_engine.events(run_id, limit=limit)

    def run_events_human(self, run_id: str, limit: int = 50):
        return self.run_engine.human_events(run_id, limit=limit)

    def run_report(self, run_id: str, max_lines: int = 12):
        return self.run_engine.report(run_id, max_lines=max_lines)
