"""Thin RunController facade for ContractCoding Runtime V4."""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ContractCoding.contract.spec import ContractSpec
    from ContractCoding.runtime.engine import AutoRunResult, RunEngine
    from ContractCoding.runtime.store import RunRecord


class RunController:
    """User-facing orchestration wrapper over the Runtime V4 kernel.

    The controller intentionally does not parse Markdown, own WorkItems, or
    persist state itself. It delegates planning to the contract layer and
    execution facts to the run store through ``RunEngine``.
    """

    def __init__(self, engine: "RunEngine"):
        self.engine = engine

    def run(self, task: str, *, max_steps: Optional[int] = None) -> "AutoRunResult":
        return self.engine.run_auto(task, max_steps=max_steps)

    def start(
        self,
        task: str,
        *,
        contract: Optional["ContractSpec"] = None,
        run_immediately: bool = False,
        max_steps: Optional[int] = None,
    ) -> str:
        return self.engine.start(
            task,
            contract=contract,
            run_immediately=run_immediately,
            max_steps=max_steps,
        )

    def resume(self, task_or_run_id: str, *, max_steps: Optional[int] = None) -> "RunRecord":
        return self.engine.resume(task_or_run_id, max_steps=max_steps)

    def status(self, task_or_run_id: str) -> dict[str, Any]:
        return self.engine.status(task_or_run_id)

    def events(self, task_or_run_id: str, *, limit: int = 50):
        return self.engine.events(task_or_run_id, limit=limit)
