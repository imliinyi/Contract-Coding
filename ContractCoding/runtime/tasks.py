"""Thin user-facing task index for ContractCoding Runtime V4."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, Optional

from ContractCoding.runtime.store import RunRecord, RunStore, TaskRecord


class TaskIndex:
    """Resolve the user's task id to the active run without owning the plan."""

    def __init__(self, store: RunStore):
        self.store = store

    def create(
        self,
        *,
        prompt: str,
        workspace_dir: str,
        backend: str,
        status_summary: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        summary = dict(status_summary or {"status": "PENDING"})
        summary.setdefault("prompt_hash", self.prompt_hash(prompt))
        task_id = self.store.create_task(
            prompt=prompt,
            workspace_dir=workspace_dir,
            backend=backend,
            status_summary=summary,
        )
        task = self.store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Unable to create task index record for {prompt!r}.")
        return task

    def attach_run(self, task_id: str, run_id: str, status: str = "PENDING") -> None:
        self.store.update_task(task_id, active_run_id=run_id, status_summary={"status": status, "run_id": run_id})
        self.store.link_run_to_task(run_id, task_id)

    def sync_from_run(self, run: RunRecord, extra: Optional[Dict[str, Any]] = None) -> None:
        task_id = str(run.metadata.get("task_id", ""))
        if not task_id:
            return
        summary = {"status": run.status, "run_id": run.id, "prompt_hash": self.prompt_hash(run.task)}
        if extra:
            summary.update(extra)
        self.store.update_task(task_id, active_run_id=run.id, status_summary=summary)

    def find_active_run_for_prompt(
        self,
        *,
        prompt: str,
        workspace_dir: str,
        backend: str = "",
    ) -> Optional[TaskRecord]:
        workspace = os.path.abspath(workspace_dir)
        task = self.store.find_active_task_by_prompt(
            prompt=prompt,
            workspace_dir=workspace,
            backend=backend,
        )
        if task is not None:
            return task
        normalized_hash = self.prompt_hash(prompt)
        for candidate in self.store.list_tasks(limit=200):
            if candidate.workspace_dir != workspace:
                continue
            if backend and candidate.backend != backend:
                continue
            if candidate.status_summary.get("prompt_hash") != normalized_hash:
                continue
            run = self.store.get_run(candidate.active_run_id)
            if run is not None and run.status in {"PENDING", "RUNNING", "PAUSED", "BLOCKED"}:
                return candidate
        return None

    def resolve_run_id(self, task_or_run_id: str) -> str:
        task = self.store.get_task(task_or_run_id)
        if task is not None:
            if not task.active_run_id:
                raise ValueError(f"Task {task_or_run_id} does not have an active run.")
            return task.active_run_id
        run = self.store.get_run(task_or_run_id)
        if run is not None:
            return run.id
        raise ValueError(f"Unknown task or run id: {task_or_run_id}")

    def task_for_run(self, run_id: str) -> Optional[TaskRecord]:
        run = self.store.get_run(run_id)
        task_id = str((run.metadata if run else {}).get("task_id", ""))
        if task_id:
            return self.store.get_task(task_id)
        return self.store.find_task_by_run(run_id)

    @staticmethod
    def prompt_hash(prompt: str) -> str:
        normalized = re.sub(r"\s+", " ", str(prompt or "").strip())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
