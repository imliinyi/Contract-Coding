"""Run execution loop for ContractCoding Runtime V4.

The controller owns user-facing orchestration. This loop owns the durable
"resume until no ready work remains" mechanics: refresh settings, ask the
scheduler for ready waves, dispatch teams, and stop at deterministic barriers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any, Optional, TYPE_CHECKING

from ContractCoding.runtime.store import RunRecord

if TYPE_CHECKING:
    from ContractCoding.runtime.engine import RunEngine


class RunLoop:
    def __init__(self, engine: "RunEngine"):
        self.engine = engine

    def resume(self, run_id: str, max_steps: Optional[int] = None) -> RunRecord:
        run_id = self.engine.resolve_run_id(run_id)
        run = self.engine._require_run(run_id)
        if run.status == "CANCELLED":
            return run

        contract = self.engine.store.get_contract(run_id)
        if contract is not None:
            self.engine.team_runtime.ensure_teams(run_id, contract)
        if max_steps is not None and max_steps <= 0:
            self.engine.store.update_run_status(
                run_id,
                "PAUSED",
                {"reason": "max_steps_reached", "max_steps": max_steps},
            )
            run = self.engine._require_run(run_id)
            self.engine.task_index.sync_from_run(run)
            return run
        self._recover_stale_running_items(run_id)
        self._recover_stale_running_gates(run_id)
        self.engine.store.update_run_status(
            run_id,
            "RUNNING",
            {
                "needs_human": False,
                "automatic_recovery_limit_reached": False,
                "contract_replan_limit_reached": False,
                "replan_reasons": [],
            },
        )
        executed = 0

        while max_steps is None or executed < max_steps:
            self.engine._refresh_runtime_settings()
            self.engine._resolve_repair_tickets(run_id)
            run = self.engine._require_run(run_id)
            if run.status in {"PAUSED", "CANCELLED"}:
                break

            contract = self.engine.store.get_contract(run_id)
            if contract is not None:
                self.engine._promote_ready_teams(run_id)

            waves = self.engine.scheduler.next_wave(run_id)
            if waves:
                dispatch_waves = waves
                if max_steps is not None:
                    dispatch_waves = self._trim_to_step_budget(waves, max_steps - executed)
                if len(dispatch_waves) > 1:
                    failed = self._execute_parallel(run, dispatch_waves)
                    executed += sum(len(wave.items) for wave in dispatch_waves)
                    if failed:
                        self.engine._finish_or_block(run_id)
                        return self.engine._require_run(run_id)
                    continue

                for wave in dispatch_waves:
                    if max_steps is not None and executed >= max_steps:
                        break
                    dispatch = wave
                    if max_steps is not None:
                        remaining = max_steps - executed
                        if remaining < len(wave.items):
                            trimmed_items = wave.items[:remaining]
                            dispatch = replace(
                                wave,
                                items=trimmed_items,
                                parallel_slots=min(wave.parallel_slots, remaining),
                                profiles=sorted({item.owner_profile for item in trimmed_items}),
                                conflict_keys=sorted({key for item in trimmed_items for key in item.conflict_keys}),
                            )
                    result = self.engine._execute_wave(run, dispatch)
                    executed += len(result.work_item_ids)
                    if not result.ok:
                        self.engine._finish_or_block(run_id)
                        return self.engine._require_run(run_id)
                continue

            if contract is not None:
                remaining = None if max_steps is None else max_steps - executed
                if remaining is not None and remaining <= 0:
                    break
                gate_results = self.engine.gate_runner.run_ready_team_gates(
                    run,
                    contract,
                    limit=remaining,
                )
                if gate_results:
                    executed += len([result for result in gate_results if result.ran])
                    if any(not result.ok for result in gate_results):
                        self.engine._finish_or_block(run_id)
                        return self.engine._require_run(run_id)
                    continue
                remaining = None if max_steps is None else max_steps - executed
                if remaining is not None and remaining <= 0:
                    break
                phase_results = self.engine.gate_runner.run_ready_phase_gates(
                    run,
                    contract,
                    limit=remaining,
                )
                if phase_results:
                    executed += len([result for result in phase_results if result.ran])
                    if any(not result.ok for result in phase_results):
                        self.engine._finish_or_block(run_id)
                        return self.engine._require_run(run_id)
                    continue
                final_result = self.engine.gate_runner.run_final_gate_if_ready(run, contract)
                if final_result.ran:
                    executed += 1
                    if not final_result.ok:
                        self.engine._finish_or_block(run_id)
                        return self.engine._require_run(run_id)
                    continue

            self.engine._finish_or_block(run_id)
            break

        run = self.engine._require_run(run_id)
        self.engine.task_index.sync_from_run(run)
        return run

    def _recover_stale_running_items(self, run_id: str) -> None:
        active_leases = self.engine.store.active_leased_items(run_id)
        for item in self.engine.store.list_work_items(run_id):
            if item.status != "RUNNING" or item.id in active_leases:
                continue
            latest = self.engine.store.latest_step_for_item(run_id, item.id)
            message = "Recovered stale RUNNING item with no active lease; retrying the item."
            if latest is not None and latest.status == "RUNNING":
                self.engine.store.finish_step(
                    latest.id,
                    "ERROR",
                    output_payload={"stale_running_recovered": True},
                    error=message,
                )
            self.engine.store.update_work_item_status(run_id, item.id, "BLOCKED", evidence=[message])
            self.engine.store.update_work_item_status(run_id, item.id, "READY", evidence=[message])
            self.engine.store.append_event(
                run_id,
                "stale_running_item_recovered",
                {"work_item_id": item.id, "latest_step_id": latest.id if latest else None},
            )

    def _recover_stale_running_gates(self, run_id: str) -> None:
        for gate in self.engine.store.list_gates(run_id):
            if gate.status != "RUNNING":
                continue
            message = "Recovered stale RUNNING gate from a previous interrupted invocation; retrying the gate."
            self.engine.store.update_gate_status(run_id, gate.gate_id, "PENDING", evidence=[message])
            self.engine.store.append_event(
                run_id,
                "stale_running_gate_recovered",
                {"gate_id": gate.gate_id, "scope_id": gate.scope_id, "gate_type": gate.gate_type},
            )

    @staticmethod
    def _trim_to_step_budget(waves: list[Any], remaining: int) -> list[Any]:
        if remaining <= 0:
            return []
        out: list[Any] = []
        budget = remaining
        first_kind = str(getattr(waves[0], "wave_kind", "")) if waves else ""
        for wave in waves:
            if first_kind and str(getattr(wave, "wave_kind", "")) != first_kind:
                break
            if budget <= 0:
                break
            if len(wave.items) <= budget:
                out.append(wave)
                budget -= len(wave.items)
                continue
            trimmed_items = wave.items[:budget]
            out.append(
                replace(
                    wave,
                    items=trimmed_items,
                    parallel_slots=min(wave.parallel_slots, budget),
                    profiles=sorted({item.owner_profile for item in trimmed_items}),
                    conflict_keys=sorted({key for item in trimmed_items for key in item.conflict_keys}),
                )
            )
            budget = 0
        return out

    def _execute_parallel(self, run: RunRecord, waves: list[Any]) -> bool:
        failed = False
        with ThreadPoolExecutor(max_workers=len(waves)) as executor:
            futures = {executor.submit(self.engine._execute_wave, run, wave): wave for wave in waves}
            for future in as_completed(futures):
                result = future.result()
                if not result.ok:
                    failed = True
        return failed
