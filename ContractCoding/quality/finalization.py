"""Final quality and recovery coordinator.

RunEngine should not know the details of integration tests, review verdicts,
or central repair transactions. This coordinator keeps that product-quality
decision outside the scheduling loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal

from ContractCoding.quality.transaction import QualityTransactionRunner

if TYPE_CHECKING:
    from ContractCoding.runtime.recovery import RecoveryCoordinator
    from ContractCoding.runtime.store import RunRecord


FinalizationStatus = Literal["completed", "repair_opened", "blocked"]


class FinalizationCoordinator:
    def __init__(
        self,
        workspace_dir: str,
        recovery: "RecoveryCoordinator",
        append_event: Callable[[str, str, dict[str, Any]], None],
        resolve_repair_transactions: Callable[["RunRecord"], None],
    ):
        self.workspace_dir = workspace_dir
        self.recovery = recovery
        self.append_event = append_event
        self.resolve_repair_transactions = resolve_repair_transactions

    def finalize(self, run: "RunRecord") -> FinalizationStatus:
        final_quality = QualityTransactionRunner(self.workspace_dir).check_integration(run.id, run.contract)
        final = final_quality.gate_result
        run.final_diagnostics = final.diagnostics
        if final.ok:
            self.resolve_repair_transactions(run)
            run.status = "COMPLETED"
            self.append_event(run.id, "final_gate_passed", {"evidence": final.evidence})
            return "completed"
        if not self.recovery.handle_final_failure(run, final.diagnostics):
            run.status = "BLOCKED"
            self.append_event(run.id, "final_gate_blocked", {"diagnostics": final.diagnostics})
            return "blocked"
        self.append_event(run.id, "repair_transaction_opened", {"diagnostics": final.diagnostics})
        return "repair_opened"
