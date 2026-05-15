"""Contract-derived team wave scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Dict, List, Set

from ..contract.kernel import ContractKernel
from ..contract.operation import ObligationStatus
from ..contract.work import TeamScheduleReport, TeamWave, TeamWorkItem, WorkStatus


@dataclass
class SchedulerConfig:
    max_parallel_teams: int = 4
    max_parallel_items_per_team: int = 1
    uncertainty_parallel_threshold: float = 0.8


@dataclass
class TeamScheduler:
    config: SchedulerConfig = field(default_factory=SchedulerConfig)

    def schedule(self, kernel: ContractKernel, *, max_per_team: int = 1) -> TeamScheduleReport:
        notes: List[str] = []
        ready: List[TeamWorkItem] = []
        blocked: List[TeamWorkItem] = []
        work_by_id = kernel.work_by_id()
        open_obligations = [
            obl for obl in kernel.obligations if obl.status == ObligationStatus.OPEN
        ]

        for item in kernel.all_work_items():
            status = item.status.value if hasattr(item.status, "value") else str(item.status)
            if status not in (WorkStatus.PENDING.value, WorkStatus.ACTIVE.value):
                continue
            if self._blocked_by_obligation(item, open_obligations):
                blocked.append(item)
                continue
            if any(self._dependency_unmet(dep, work_by_id) for dep in item.dependency_ids):
                blocked.append(item)
                continue
            ready.append(item)

        waves = self._pack_waves(ready, max_per_team=max_per_team)
        if not waves:
            notes.append("no schedulable work items")
        else:
            notes.append(f"scheduled {sum(len(w.items) for w in waves)} items across {len(waves)} waves")
        if blocked:
            notes.append(f"blocked {len(blocked)} items")
        return TeamScheduleReport(
            schedule_id=f"sched:{uuid.uuid4().hex[:10]}",
            waves=waves,
            blocked=blocked,
            notes=notes,
        )

    # ------------------------------------------------------------------ pack

    def _pack_waves(self, items: List[TeamWorkItem], *, max_per_team: int) -> List[TeamWave]:
        waves: List[TeamWave] = []
        current: List[TeamWorkItem] = []
        per_team: Dict[str, int] = {}
        limit_per_team = min(max_per_team, self.config.max_parallel_items_per_team)
        for item in items:
            if not self._parallel_safe(item):
                if current:
                    waves.append(self._wave(current, "parallel-safe pack"))
                    current = []
                    per_team = {}
                waves.append(self._wave([item], "serialized due to uncertainty or item policy"))
                continue
            if not self._fits(item, current, per_team, limit_per_team):
                if current:
                    waves.append(self._wave(current, "parallel-safe pack"))
                current = [item]
                per_team = {item.team_id: 1}
                continue
            current.append(item)
            per_team[item.team_id] = per_team.get(item.team_id, 0) + 1
        if current:
            waves.append(self._wave(current, "parallel-safe pack"))
        return waves

    def _wave(self, items: List[TeamWorkItem], reason: str) -> TeamWave:
        return TeamWave(wave_id=f"wave:{uuid.uuid4().hex[:10]}", items=list(items), reason=reason)

    def _parallel_safe(self, item: TeamWorkItem) -> bool:
        return bool(item.parallel_safe) and float(item.uncertainty) <= self.config.uncertainty_parallel_threshold

    def _fits(
        self,
        item: TeamWorkItem,
        current: List[TeamWorkItem],
        per_team: Dict[str, int],
        limit_per_team: int,
    ) -> bool:
        if len({i.team_id for i in current} | {item.team_id}) > self.config.max_parallel_teams:
            return False
        if per_team.get(item.team_id, 0) >= limit_per_team:
            return False
        return not any(self._conflicts(item, other) for other in current)

    def _conflicts(self, left: TeamWorkItem, right: TeamWorkItem) -> bool:
        left_writes = set(self._norm_all(left.writes))
        right_writes = set(self._norm_all(right.writes))
        left_reads = set(self._norm_all(left.reads))
        right_reads = set(self._norm_all(right.reads))
        if left_writes & right_writes:
            return True
        if left_writes & right_reads:
            return True
        if right_writes & left_reads:
            return True
        left_keys = {k.render() for k in left.conflict_keys}
        right_keys = {k.render() for k in right.conflict_keys}
        return bool(left_keys & right_keys)

    def _norm_all(self, paths: List[str]) -> Set[str]:
        return {p.strip().strip("/") for p in paths if p.strip()}

    def _dependency_unmet(self, dep: str, work_by_id: Dict[str, TeamWorkItem]) -> bool:
        item = work_by_id.get(dep)
        if item is None:
            return True
        status = item.status.value if hasattr(item.status, "value") else str(item.status)
        return status != WorkStatus.DONE.value

    def _blocked_by_obligation(self, item: TeamWorkItem, obligations: List[object]) -> bool:
        ids = {item.work_id, item.task_id, f"work:{item.work_id}", f"work:{item.task_id}"}
        for obligation in obligations:
            task_ids = set(getattr(obligation, "task_ids", []) or [])
            target_ref = str(getattr(obligation, "target_ref", ""))
            if task_ids & {item.task_id, item.work_id}:
                return True
            if target_ref in ids:
                return True
            if target_ref and target_ref in set(item.capsule_dependencies):
                return True
        return False
