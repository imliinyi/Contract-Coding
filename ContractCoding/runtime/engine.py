"""OpenAI-first Product Kernel long-running runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
import threading
from typing import Any, Dict, List

from ContractCoding.config import Config
from ContractCoding.contract.compiler import ContractCompiler
from ContractCoding.contract.spec import WorkItem
from ContractCoding.contract.store import ContractFileStore
from ContractCoding.quality.finalization import FinalizationCoordinator
from ContractCoding.runtime.monitor import RunMonitor
from ContractCoding.runtime.recovery import RecoveryCoordinator
from ContractCoding.runtime.scheduler import Scheduler, TeamWave
from ContractCoding.runtime.store import RunRecord, RunStore
from ContractCoding.runtime.team import TeamRuntime
from ContractCoding.runtime.worker import DeterministicWorker, OpenAIWorker


@dataclass
class AutoRunResult:
    task_id: str
    run_id: str
    status: str
    report: str


def _dedupe_runtime(values: List[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


class RunEngine:
    def __init__(self, config: Config | None = None, worker: Any | None = None):
        self.config = config or Config()
        self.workspace_dir = os.path.abspath(self.config.WORKSPACE_DIR)
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.store = RunStore(self.workspace_dir, getattr(self.config, "RUN_STORE_PATH", ""))
        self.compiler = ContractCompiler()
        self.scheduler = Scheduler()
        self.monitor = RunMonitor(self.workspace_dir)
        self.team_runtime = TeamRuntime(self.workspace_dir)
        self.recovery = RecoveryCoordinator(
            self.workspace_dir,
            max_attempts=int(getattr(self.config, "AUTO_TEST_REPAIR_MAX", 2) or 2),
            max_replans=int(getattr(self.config, "AUTO_CONTRACT_REPLAN_MAX", 1) or 1),
        )
        self.finalization = FinalizationCoordinator(
            self.workspace_dir,
            self.recovery,
            self._append_event,
            self._resolve_repair_transactions,
        )
        self.worker = worker
        self._event_lock = threading.Lock()
        self._save_lock = threading.Lock()

    def run(self, task: str, max_steps: int | None = None, offline: bool = False) -> AutoRunResult:
        contract = self.compiler.compile(task)
        ContractFileStore(self.workspace_dir).write(contract)
        run = self.store.create_run(task, contract)
        return self.resume(run.id, max_steps=max_steps, offline=offline)

    def resume(self, run_id: str, max_steps: int | None = None, offline: bool = False) -> AutoRunResult:
        run = self.store.get(run_id)
        if run.status == "COMPLETED":
            return self._result(run)
        if run.status == "BLOCKED":
            reopened = self._route_blocked_dependency_items(run)
            reopened = self._reopen_retryable_infra_items(run) or reopened
            reopened = self._reopen_retryable_local_items(run) or reopened
            if not reopened:
                return self._result(run)
        run.status = "RUNNING"
        budget = max(1, int(max_steps if max_steps is not None else getattr(self.config, "AUTO_MAX_STEWARD_LOOPS", 12)))
        used = 0
        while used < budget:
            team_waves = self.scheduler.ready_team_waves(
                run.contract,
                max_teams=int(getattr(self.config, "MAX_PARALLEL_TEAMS", 4) or 4),
                max_items_per_team=int(getattr(self.config, "MAX_PARALLEL_ITEMS_PER_TEAM", 3) or 3),
            )
            if team_waves:
                team_waves = self._trim_team_waves(team_waves, max(1, budget - used))
                ready = [item for wave in team_waves for item in wave.items]
                self._append_event(
                    run.id,
                    "ready_team_wave",
                    {
                        "items": [item.id for item in ready],
                        "phase": ready[0].phase,
                        "parallelism": len(ready),
                        "feature_teams": _dedupe_runtime([item.feature_team_id for item in ready]),
                        "teams": [wave.to_record() for wave in team_waves],
                    },
                )
                if len(team_waves) == 1 and len(team_waves[0].items) == 1:
                    self._execute_item(run, team_waves[0].items[0], offline=offline)
                    used += 1
                    run.steps += 1
                    self._save(run)
                    continue
                with ThreadPoolExecutor(max_workers=len(team_waves)) as executor:
                    futures = {executor.submit(self._execute_team_wave, run, wave, offline): wave for wave in team_waves}
                    for future in as_completed(futures):
                        wave = futures[future]
                        try:
                            completed_count = int(future.result() or 0)
                        except Exception as exc:
                            completed_count = 0
                            for item in wave.items:
                                item.status = "BLOCKED"
                                item.diagnostics = [
                                    {
                                        "code": "runtime_item_exception",
                                        "slice_id": item.slice_id,
                                        "work_item_id": item.id,
                                        "message": str(exc),
                                    }
                                ]
                            self._append_event(
                                run.id,
                                "team_wave_blocked",
                                {"team": wave.to_record(), "message": str(exc)},
                            )
                        used += completed_count
                        run.steps += completed_count
                        self._save(run)
                continue
            if self.scheduler.blocked_items(run.contract):
                run.status = "BLOCKED"
                self._append_event(run.id, "run_blocked", {"items": [item.id for item in self.scheduler.blocked_items(run.contract)]})
                self._save(run)
                return self._result(run)
            if self.scheduler.is_complete(run.contract):
                final_status = self.finalization.finalize(run)
                self._save(run)
                if final_status in {"completed", "blocked"}:
                    return self._result(run)
                continue
            break
        if run.status == "RUNNING":
            run.status = "PAUSED"
        self._save(run)
        self.monitor.write(run)
        return self._result(run)

    def status(self, run_id: str) -> Dict[str, Any]:
        run = self.store.get(run_id)
        return self.monitor.snapshot(run)

    def graph(self, run_id: str) -> Dict[str, Any]:
        run = self.store.get(run_id)
        return {
            "run_id": run.id,
            "status": run.status,
            "kernel": run.contract.product_kernel.to_record(),
            "canonical_substrate": run.contract.canonical_substrate.to_record(),
            "feature_teams": [team.to_record() for team in run.contract.feature_teams],
            "team_subcontracts": [subcontract.to_record() for subcontract in run.contract.team_subcontracts],
            "interface_capsules": [capsule.to_record() for capsule in run.contract.interface_capsules],
            "slices": [feature_slice.to_record() for feature_slice in run.contract.feature_slices],
            "items": [item.to_record() for item in run.contract.work_items],
            "teams": [team.to_record() for team in run.contract.teams],
            "team_states": [state.to_record() for state in run.contract.team_states],
            "promotions": [promotion.to_record() for promotion in run.contract.promotions],
            "quality_transactions": [transaction.to_record() for transaction in run.contract.quality_transactions],
            "repair_transactions": [transaction.to_record() for transaction in run.contract.repair_transactions],
            "replans": [replan.to_record() for replan in run.contract.replans],
            "llm_telemetry": run.contract.llm_telemetry.to_record(),
        }

    def events(self, run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.store.events(run_id, limit=limit)

    def _execute_item(self, run: RunRecord, item: WorkItem, offline: bool) -> None:
        item.status = "RUNNING"
        item.attempts += 1
        self._append_event(
            run.id,
            "item_started",
            {"id": item.id, "slice_id": item.slice_id, "team_id": item.team_id, "feature_team_id": item.feature_team_id},
        )
        worker = self._worker(offline)
        team_result = self.team_runtime.execute(run.id, run.contract, item, worker)
        if not team_result.ok:
            item.diagnostics = list(team_result.diagnostics)
            if item.kind == "repair" and self.recovery.handle_repair_failure(run, item, item.diagnostics):
                self._sync_team_states(run)
                self._append_event(
                    run.id,
                    "repair_transaction_continued",
                    {"id": item.id, "diagnostics": item.diagnostics, "status": item.status},
                )
                return
            if self.recovery.handle_item_blocker(run, item, item.diagnostics):
                self._sync_team_states(run)
                self._append_event(
                    run.id,
                    "repair_transaction_opened_from_blocker",
                    {"id": item.id, "diagnostics": item.diagnostics, "status": item.status},
                )
                return
            item.status = "BLOCKED"
            self._sync_team_states(run)
            self._append_event(run.id, "item_blocked", {"id": item.id, "diagnostics": item.diagnostics})
            return
        item.evidence = list(team_result.evidence)
        item.diagnostics = []
        item.status = "VERIFIED"
        self._sync_team_states(run)
        self._append_event(
            run.id,
            "item_verified",
            {
                "id": item.id,
                "team_id": item.team_id,
                "feature_team_id": item.feature_team_id,
                "promotion": team_result.promotion.to_record() if team_result.promotion else {},
            },
        )

    def _execute_team_wave(self, run: RunRecord, wave: TeamWave, offline: bool) -> int:
        self._append_event(
            run.id,
            "team_wave_started",
            {
                **wave.to_record(),
                "execution": "internal_parallel" if wave.internal_parallel else "internal_serial",
            },
        )
        if not wave.internal_parallel:
            for item in wave.items:
                self._execute_item(run, item, offline=offline)
            return len(wave.items)
        with ThreadPoolExecutor(max_workers=len(wave.items)) as executor:
            futures = {executor.submit(self._execute_item, run, item, offline): item for item in wave.items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    item.status = "BLOCKED"
                    item.diagnostics = [
                        {
                            "code": "runtime_item_exception",
                            "slice_id": item.slice_id,
                            "work_item_id": item.id,
                            "message": str(exc),
                        }
                    ]
                    self._sync_team_states(run)
                    self._append_event(run.id, "item_blocked", {"id": item.id, "diagnostics": item.diagnostics})
        return len(wave.items)

    @staticmethod
    def _trim_team_waves(waves: List[TeamWave], remaining: int) -> List[TeamWave]:
        trimmed: List[TeamWave] = []
        left = max(1, int(remaining or 1))
        for wave in waves:
            if left <= 0:
                break
            items = wave.items[:left]
            if not items:
                continue
            trimmed.append(
                TeamWave(
                    feature_team_id=wave.feature_team_id,
                    team_id=wave.team_id,
                    items=items,
                    internal_parallel=wave.internal_parallel and len(items) > 1,
                    phase=wave.phase,
                )
            )
            left -= len(items)
        return trimmed

    def _append_event(self, run_id: str, event_type: str, payload: Dict[str, Any] | None = None) -> None:
        with self._event_lock:
            self.store.append_event(run_id, event_type, payload or {})

    def _save(self, run: RunRecord) -> None:
        with self._save_lock:
            self._sync_team_states(run)
            self.store.save(run)
            self.monitor.write(run)

    def _worker(self, offline: bool):
        if self.worker is not None:
            return self.worker
        if offline or os.getenv("CONTRACTCODING_OFFLINE_WORKER", "").lower() in {"1", "true", "yes"}:
            return DeterministicWorker()
        return OpenAIWorker(self.config)

    def _resolve_repair_transactions(self, run: RunRecord) -> None:
        changed = False
        for transaction in run.contract.repair_transactions:
            if transaction.status in {"OPEN", "PATCH_VALIDATED", "REPLANNED"}:
                transaction.status = "RESOLVED"
                transaction.evidence.append("final integration gate passed after repair/replan")
                self.recovery.write_transaction(run.id, transaction)
                changed = True
        if changed:
            self._append_event(run.id, "repair_transactions_resolved", {})

    def _reopen_retryable_infra_items(self, run: RunRecord) -> bool:
        max_retries = max(0, int(getattr(self.config, "AUTO_INFRA_RETRY_MAX", 2) or 0))
        reopened: List[str] = []
        for item in run.contract.work_items:
            if item.status != "BLOCKED" or item.attempts > max_retries:
                continue
            if not any(str(diag.get("code", "")) == "worker_infra_failure" for diag in item.diagnostics):
                continue
            item.status = "PENDING"
            item.diagnostics = []
            reopened.append(item.id)
        if not reopened:
            return False
        run.status = "PAUSED"
        self._append_event(run.id, "infra_retry_reopened", {"items": reopened, "max_retries": max_retries})
        self._save(run)
        return True

    def _route_blocked_dependency_items(self, run: RunRecord) -> bool:
        routed: List[str] = []
        for item in run.contract.work_items:
            if item.status != "BLOCKED" or item.kind == "repair":
                continue
            if not self.recovery.handle_item_blocker(run, item, item.diagnostics):
                continue
            routed.append(item.id)
        if not routed:
            return False
        run.status = "PAUSED"
        self._append_event(run.id, "blocked_dependency_routed_to_repair", {"items": routed})
        self._save(run)
        return True

    def _reopen_retryable_local_items(self, run: RunRecord) -> bool:
        max_attempts = max(1, int(getattr(self.config, "AUTO_ITEM_REPAIR_MAX", getattr(self.config, "AUTO_RETRY_MAX_PER_ITEM", 2)) or 2))
        reopened: List[str] = []
        for item in run.contract.work_items:
            if item.status != "BLOCKED" or item.kind == "repair":
                continue
            if item.attempts >= max_attempts:
                continue
            if not self._is_retryable_local_failure(item):
                continue
            item.status = "PENDING"
            item.evidence.append("reopened for local slice retry after gate failure")
            reopened.append(item.id)
        if not reopened:
            return False
        run.status = "PAUSED"
        self._append_event(run.id, "local_retry_reopened", {"items": reopened, "max_attempts": max_attempts})
        self._save(run)
        return True

    @staticmethod
    def _is_retryable_local_failure(item: WorkItem) -> bool:
        retryable_codes = {
            "syntax_error",
            "slice_smoke_import_failed",
            "promotion_blocked",
            "missing_artifact",
            "placeholder_detected",
        }
        return any(str(diagnostic.get("code", "")) in retryable_codes for diagnostic in item.diagnostics)

    def _result(self, run: RunRecord) -> AutoRunResult:
        return AutoRunResult(
            task_id=run.id,
            run_id=run.id,
            status=run.status,
            report=self.monitor.snapshot(run)["report"],
        )

    def _sync_team_states(self, run: RunRecord) -> None:
        contract = run.contract
        verified = {
            item.slice_id
            for item in contract.work_items
            if item.status == "VERIFIED"
        }
        capsule_by_team = {capsule.team_id: capsule for capsule in contract.interface_capsules}
        by_team_items: Dict[str, List[WorkItem]] = {}
        for item in contract.work_items:
            by_team_items.setdefault(item.feature_team_id or item.slice_id, []).append(item)
        known = {state.team_id: state for state in contract.team_states}
        for team in contract.feature_teams:
            state = known.get(team.id)
            if state is None:
                continue
            items = by_team_items.get(team.id, [])
            active = [item.id for item in items if item.status == "RUNNING"]
            ready = [
                item.id
                for item in items
                if item.status in {"PENDING", "READY"}
                and all(dep in verified or dep in contract.item_by_id() and contract.item_by_id()[dep].status == "VERIFIED" for dep in item.dependencies)
            ]
            capsule = capsule_by_team.get(team.id)
            state.interface_refs = [capsule.id] if capsule else []
            state.frozen_interfaces = [capsule.id] if capsule and capsule.status == "LOCKED" else []
            state.active_item_ids = active
            state.ready_item_ids = ready
            waiting: List[str] = []
            for item in items:
                if item.status not in {"PENDING", "READY", "RUNNING"}:
                    continue
                for dependency in item.dependencies:
                    if dependency.startswith("capsule:") and dependency not in verified:
                        waiting.append(dependency)
            for dependency_team in team.dependencies:
                capsule_ref = f"capsule:{dependency_team}"
                if capsule_ref not in verified:
                    waiting.append(capsule_ref)
            state.waiting_on_interfaces = _dedupe_runtime(waiting)
            if active:
                state.phase = "running"
            elif state.ready_item_ids:
                next_item = next((item for item in items if item.id in state.ready_item_ids), None)
                state.phase = "capsule" if next_item and next_item.kind in {"capsule", "interface"} else "build"
            elif items and all(item.status == "VERIFIED" for item in items):
                state.phase = "verified"
            elif state.waiting_on_interfaces:
                state.phase = "waiting_on_capsule"
            else:
                state.phase = "planned"
