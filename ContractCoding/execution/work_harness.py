"""Generalized work-item harness for Runtime V4 execution.

`TaskHarness` handles the concrete tool/validation step. This wrapper is the
runtime-facing boundary that adds evidence collection and work-item validation.
"""

from __future__ import annotations

import os
from typing import List

from ContractCoding.config import Config
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.execution.harness import TaskHarness, TaskResult
from ContractCoding.runtime.evidence import EvidenceCollector


class WorkHarness:
    def __init__(self, config: Config):
        self.config = config
        self.task_harness = TaskHarness(config=config)
        self.evidence_collector = EvidenceCollector()

    def execute(self, agent, agent_name: str, state, next_available_agents: list, context_manager) -> TaskResult:
        result = self.task_harness.execute(
            agent=agent,
            agent_name=agent_name,
            state=state,
            next_available_agents=next_available_agents,
            context_manager=context_manager,
        )
        self.evidence_collector.extend_from_task_result(result)
        return result

    def validate_work_item_artifacts(self, item: WorkItem, workspace_dir: str | None = None) -> List[str]:
        workspace = os.path.abspath(workspace_dir or self.config.WORKSPACE_DIR)
        errors: List[str] = []
        for artifact in item.target_artifacts:
            if item.kind in {"coding", "doc", "data"}:
                if not os.path.exists(os.path.join(workspace, artifact)):
                    errors.append(f"Target artifact missing for work item {item.id}: {artifact}")
        return errors
