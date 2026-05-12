"""Dependency scheduler for feature-slice work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from ContractCoding.contract.spec import ContractSpec, WorkItem


COMPLETE_STATUSES = {"VERIFIED", "SUPERSEDED"}


@dataclass
class TeamWave:
    """A schedulable feature-team batch.

    Items inside a wave belong to the same feature team. The runtime may run
    them in parallel only when the slice graph and conflict keys say it is safe.
    """

    feature_team_id: str
    team_id: str
    items: List[WorkItem]
    internal_parallel: bool
    phase: str

    def to_record(self) -> dict:
        return {
            "feature_team_id": self.feature_team_id,
            "team_id": self.team_id,
            "items": [item.id for item in self.items],
            "slice_ids": [item.slice_id for item in self.items],
            "internal_parallel": self.internal_parallel,
            "phase": self.phase,
        }


class Scheduler:
    def ready_items(self, contract: ContractSpec, limit: int = 4) -> List[WorkItem]:
        return [
            item
            for wave in self.ready_team_waves(contract, max_teams=limit, max_items_per_team=limit)
            for item in wave.items
        ]

    def ready_wave(self, contract: ContractSpec, limit: int = 4) -> List[WorkItem]:
        return self.ready_items(contract, limit=limit)

    def ready_team_waves(
        self,
        contract: ContractSpec,
        max_teams: int = 4,
        max_items_per_team: int = 3,
    ) -> List[TeamWave]:
        by_id = contract.item_by_id()
        verified_slices = {item.slice_id for item in contract.work_items if item.status == "VERIFIED"}
        candidates: List[WorkItem] = []
        for item in contract.work_items:
            if item.status not in {"PENDING", "READY"}:
                continue
            deps_ok = all(
                dep in verified_slices or by_id.get(dep, None) and by_id[dep].status in COMPLETE_STATUSES
                for dep in item.dependencies
            )
            if deps_ok:
                candidates.append(item)
        if not candidates:
            return []
        active_phase = candidates[0].phase
        waves: List[TeamWave] = []
        claimed: Set[str] = set()
        team_index: dict[str, TeamWave] = {}
        for item in candidates:
            if item.phase != active_phase:
                continue
            conflict_keys = set(item.conflict_keys or [f"artifact:{path}" for path in item.allowed_artifacts])
            if claimed.intersection(conflict_keys):
                continue
            feature_team_id = item.feature_team_id or item.slice_id
            team_id = item.team_id or f"team:{feature_team_id}"
            wave = team_index.get(feature_team_id)
            if wave is None:
                if len(waves) >= max(1, int(max_teams or 1)):
                    continue
                wave = TeamWave(
                    feature_team_id=feature_team_id,
                    team_id=team_id,
                    items=[],
                    internal_parallel=False,
                    phase=active_phase,
                )
                team_index[feature_team_id] = wave
                waves.append(wave)
            if len(wave.items) >= max(1, int(max_items_per_team or 1)):
                continue
            wave.items.append(item)
            claimed.update(conflict_keys)
        for wave in waves:
            wave.internal_parallel = self._can_run_inside_team_in_parallel(wave.items)
        return waves

    @staticmethod
    def _can_run_inside_team_in_parallel(items: List[WorkItem]) -> bool:
        if len(items) <= 1:
            return False
        slice_ids = {item.slice_id for item in items}
        if any(item.kind == "repair" for item in items):
            return False
        return not any(any(dependency in slice_ids for dependency in item.dependencies) for item in items)

    @staticmethod
    def is_complete(contract: ContractSpec) -> bool:
        return bool(contract.work_items) and all(item.status in COMPLETE_STATUSES for item in contract.work_items)

    @staticmethod
    def blocked_items(contract: ContractSpec) -> List[WorkItem]:
        return [item for item in contract.work_items if item.status == "BLOCKED"]
