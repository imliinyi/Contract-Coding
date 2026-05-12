"""Run monitor snapshots."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from ContractCoding.runtime.store import RunRecord
from ContractCoding.runtime.scheduler import Scheduler


class RunMonitor:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def snapshot(self, run: RunRecord) -> Dict[str, Any]:
        contract = run.contract
        items = [item.to_record() for item in contract.work_items]
        ready_team_waves = Scheduler().ready_team_waves(contract, max_teams=8, max_items_per_team=8)
        ready = [item for wave in ready_team_waves for item in wave.items]
        active_phase = next((item.phase for item in contract.work_items if item.status in {"RUNNING", "READY", "PENDING"}), "final")
        snapshot = {
            "run": {
                "id": run.id,
                "status": run.status,
                "steps": run.steps,
                "updated_at": run.updated_at,
                "phase": active_phase,
            },
            "kernel": {
                "status": contract.product_kernel.status,
                "canonical_substrate": contract.canonical_substrate.to_record(),
                "acceptance": [row.get("id") for row in contract.product_kernel.acceptance_matrix],
                "invariants": [row.get("id") for row in contract.product_kernel.invariants],
                "semantic_invariants": [row.get("id") for row in contract.product_kernel.semantic_invariants],
            },
            "ready_wave": [item.id for item in ready],
            "ready_team_waves": [wave.to_record() for wave in ready_team_waves],
            "feature_teams": [
                {
                    **team.to_record(),
                    "slice_status": self._team_slice_status(run, team.id),
                    "ready_items": [item.id for item in ready if item.feature_team_id == team.id],
                    "internal_parallel_ready": next(
                        (wave.internal_parallel for wave in ready_team_waves if wave.feature_team_id == team.id),
                        False,
                    ),
                }
                for team in contract.feature_teams
            ],
            "team_subcontracts": [subcontract.to_record() for subcontract in contract.team_subcontracts],
            "interface_capsules": [
                {
                    "id": capsule.id,
                    "team_id": capsule.team_id,
                    "version": capsule.version,
                    "status": capsule.status,
                    "consumer_team_ids": list(capsule.consumer_team_ids),
                    "producer_slice_ids": list(capsule.producer_slice_ids),
                    "capabilities": list(capsule.capabilities),
                    "public_modules": list(capsule.public_modules),
                    "canonical_imports": dict(capsule.canonical_imports),
                    "smoke": [row.get("id") for row in capsule.smoke],
                }
                for capsule in contract.interface_capsules
            ],
            "slices": [
                {
                    "id": feature_slice.id,
                    "title": feature_slice.title,
                    "feature_team_id": feature_slice.feature_team_id,
                    "owners": list(feature_slice.owner_artifacts),
                    "dependencies": list(feature_slice.dependencies),
                    "smoke": [row.get("id") for row in feature_slice.slice_smoke],
                    "size_budget": dict((feature_slice.interface_contract or {}).get("size_budget", {}) or {}),
                    "status": next((item.status for item in contract.work_items if item.slice_id == feature_slice.id), "GATE_ONLY"),
                }
                for feature_slice in contract.feature_slices
            ],
            "items": items,
            "teams": [team.to_record() for team in contract.teams],
            "team_states": [state.to_record() for state in contract.team_states],
            "promotions": [promotion.to_record() for promotion in contract.promotions],
            "quality_transactions": [transaction.to_record() for transaction in contract.quality_transactions],
            "repair_transactions": [transaction.to_record() for transaction in contract.repair_transactions],
            "replans": [replan.to_record() for replan in contract.replans],
            "llm_telemetry": contract.llm_telemetry.to_record(),
            "quality_signals": self._quality_signals(run),
            "latest_diagnostics": list(run.final_diagnostics[-5:]),
            "report": self._report(run),
        }
        return snapshot

    def write(self, run: RunRecord) -> str:
        path = os.path.join(self.workspace_dir, ".contractcoding", "monitor", f"{run.id}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.snapshot(run), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def _quality_signals(self, run: RunRecord) -> Dict[str, Any]:
        contract = run.contract
        project_budget = next(
            (row for row in contract.product_kernel.semantic_invariants if row.get("kind") == "loc_budget"),
            {},
        )
        project_min = int(project_budget.get("min_total_loc", 0) or 0)
        project_loc = self._count_loc(contract.required_artifacts)
        slices = []
        for feature_slice in contract.feature_slices:
            budget = dict((feature_slice.interface_contract or {}).get("size_budget", {}) or {})
            minimum = int(budget.get("min_total_loc", 0) or 0) if budget.get("enabled") else 0
            loc = self._count_loc(feature_slice.owner_artifacts)
            if minimum:
                slices.append(
                    {
                        "slice_id": feature_slice.id,
                        "actual_loc": loc,
                        "target_loc": minimum,
                        "status": "met" if loc >= minimum else "below_target",
                        "hard_gate": False,
                    }
                )
        return {
            "project_loc": {
                "actual": project_loc,
                "target": project_min,
                "status": "met" if project_min and project_loc >= project_min else ("below_target" if project_min else "not_requested"),
                "hard_gate": False,
            },
            "slice_loc": slices,
        }

    def _count_loc(self, artifacts: list[str]) -> int:
        total = 0
        for artifact in artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except OSError:
                continue
            total += sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))
        return total

    @staticmethod
    def _team_slice_status(run: RunRecord, team_id: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in run.contract.work_items:
            if item.feature_team_id != team_id:
                continue
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    @staticmethod
    def _report(run: RunRecord) -> str:
        counts: Dict[str, int] = {}
        for item in run.contract.work_items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return (
            f"Run {run.id} {run.status}; items={counts}; "
            f"teams={len(run.contract.teams)} promotions={len(run.contract.promotions)} "
            f"repairs={len(run.contract.repair_transactions)} replans={len(run.contract.replans)}"
        )
