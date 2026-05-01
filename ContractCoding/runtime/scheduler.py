"""Contract scheduler for serial and parallel team waves."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from ContractCoding.contract.spec import ContractSpec, WorkScope
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.runtime.store import RunStore


ACTIVE_STATUSES = {"PENDING", "READY"}
COMPLETED_STATUSES = {"DONE", "VERIFIED"}
TERMINAL_STATUSES = {"VERIFIED"}


@dataclass
class TeamWave:
    run_id: str
    scope: WorkScope
    items: List[WorkItem]
    execution_plane: str
    profiles: List[str]
    parallel_slots: int
    promotion_barrier: bool = False
    conflict_keys: List[str] = field(default_factory=list)
    wave_kind: str = "implementation"
    parallel_reason: str = ""
    serial_reason: str = ""

    @property
    def team_key(self) -> str:
        item_ids = "-".join(item.id for item in self.items)
        return f"{self.wave_kind}:{self.scope.id}:{item_ids}"


@dataclass
class BlockedReason:
    work_item_id: str
    reason: str


class Scheduler:
    """Build ready waves from the compiled contract and runtime state."""

    def __init__(self, store: RunStore):
        self.store = store
        self.runtime_overrides: Dict[str, object] = {}

    def next_wave(self, run_id: str) -> List[TeamWave]:
        contract = self._require_contract(run_id)
        states = {item.id: item for item in self.store.list_work_items(run_id)}
        items = [self._stateful_item(item, states.get(item.id)) for item in contract.work_items]
        item_by_id = {item.id: item for item in items}
        status_by_id = {item.id: item.status for item in items}
        leased = self.store.active_leased_items(run_id)
        active_phase_id = self._active_phase_id(run_id, contract, items, status_by_id)

        implementation_items = [
            item
            for item in items
            if item.status in ACTIVE_STATUSES
            and item.id not in leased
            and self._item_in_active_phase(item, active_phase_id)
            and self._milestone_ready_for_item(contract, item)
            and self._dependencies_satisfied_for_implementation(item, item_by_id, status_by_id)
        ]
        max_teams = int(
            self.runtime_overrides.get("max_parallel_teams", contract.execution_policy.get("max_parallel_teams", 4)) or 4
        )
        implementation_waves = self._group_waves(
            run_id=run_id,
            contract=contract,
            candidates=implementation_items,
            wave_kind="implementation",
            max_waves=max_teams,
        )
        return implementation_waves

    def blocked_reasons(self, run_id: str) -> List[BlockedReason]:
        contract = self._require_contract(run_id)
        states = {item.id: item for item in self.store.list_work_items(run_id)}
        items = [self._stateful_item(item, states.get(item.id)) for item in contract.work_items]
        item_by_id = {item.id: item for item in items}
        status_by_id = {item.id: item.status for item in items}
        leased = self.store.active_leased_items(run_id)
        active_phase_id = self._active_phase_id(run_id, contract, items, status_by_id)
        out: List[BlockedReason] = []
        for item in items:
            if item.status == "BLOCKED":
                out.append(BlockedReason(item.id, "Item is BLOCKED; retry, replan, or manual recovery required."))
                continue
            if item.status not in ACTIVE_STATUSES:
                continue
            if not self._item_in_active_phase(item, active_phase_id):
                item_phase = self._phase_id_for_item(item)
                out.append(
                    BlockedReason(
                        item.id,
                        f"Waiting for active phase `{active_phase_id}` before phase `{item_phase}` can run.",
                    )
                )
                continue
            missing = [
                dependency
                for dependency in item.depends_on
                if not self._dependency_satisfied_for_implementation(
                    item,
                    dependency,
                    item_by_id,
                    status_by_id,
                )
            ]
            if missing:
                reason = "Waiting on dependencies"
                if item.dependency_policy == "interface":
                    reason = "Waiting on dependencies without declared stable interfaces"
                out.append(BlockedReason(item.id, f"{reason}: {', '.join(missing)}"))
            elif not self._milestone_ready_for_item(contract, item):
                out.append(BlockedReason(item.id, self._milestone_blocked_reason(contract, item)))
            elif item.id in leased:
                out.append(BlockedReason(item.id, "Leased by another team run."))
        return out

    def _active_phase_id(
        self,
        run_id: str,
        contract: ContractSpec,
        items: List[WorkItem],
        status_by_id: Dict[str, str],
    ) -> str:
        if not contract.phase_plan:
            return ""
        items_by_phase: Dict[str, List[WorkItem]] = {}
        for item in items:
            phase_id = self._phase_id_for_item(item)
            if phase_id:
                items_by_phase.setdefault(phase_id, []).append(item)
        fallback = ""
        for phase in contract.phase_plan:
            phase_id = phase.phase_id
            if not phase_id:
                continue
            phase_items = items_by_phase.get(phase_id, [])
            gate = self.store.get_gate(run_id, f"phase:{phase_id}")
            if not phase_items:
                continue
            fallback = fallback or phase_id
            if any(status_by_id.get(item.id, item.status) not in TERMINAL_STATUSES for item in phase_items):
                return phase_id
            if gate is not None and gate.status != "PASSED":
                return phase_id
        return ""

    @staticmethod
    def _phase_id_for_item(item: WorkItem) -> str:
        return str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")).strip()

    def _item_in_active_phase(self, item: WorkItem, active_phase_id: str) -> bool:
        if not active_phase_id:
            return True
        item_phase = self._phase_id_for_item(item)
        return not item_phase or item_phase == active_phase_id

    def _milestone_ready_for_item(self, contract: ContractSpec, item: WorkItem) -> bool:
        if not contract.milestones and not contract.interfaces:
            return True
        if item.id.startswith(("scaffold:", "interface:")):
            return True
        if item.kind != "coding":
            return True
        if not contract.critical_interfaces_frozen():
            return False
        return contract.team_interface_ready(item.scope_id)

    def _milestone_blocked_reason(self, contract: ContractSpec, item: WorkItem) -> str:
        if item.kind == "coding" and not contract.critical_interfaces_frozen():
            open_ids = [
                interface.id
                for interface in contract.critical_interfaces()
                if interface.status not in contract.build_ready_interface_statuses()
            ]
            return "Waiting on critical interface freeze: " + ", ".join(open_ids)
        if item.kind == "coding" and not contract.team_interface_ready(item.scope_id):
            open_ids = [
                interface.id
                for interface in contract.interfaces_for_scope(item.scope_id)
                if interface.status not in contract.build_ready_interface_statuses()
            ]
            return "Waiting on team-local interface freeze: " + ", ".join(open_ids or [item.scope_id])
        return "Waiting on progressive contract milestone readiness."

    def _group_waves(
        self,
        run_id: str,
        contract: ContractSpec,
        candidates: Iterable[WorkItem],
        wave_kind: str,
        occupied_conflicts: Optional[Set[str]] = None,
        max_waves: Optional[int] = None,
    ) -> List[TeamWave]:
        scope_by_id = contract.scope_by_id()
        max_teams = int(
            self.runtime_overrides.get("max_parallel_teams", contract.execution_policy.get("max_parallel_teams", 4)) or 4
        )
        max_items = int(
            self.runtime_overrides.get(
                "max_parallel_items_per_team",
                contract.execution_policy.get("max_parallel_items_per_team", 4),
            )
            or 4
        )

        waves: List[TeamWave] = []
        occupied_conflicts = set(occupied_conflicts or set())
        occupied_serial_groups: Set[str] = set()
        by_scope: Dict[str, List[WorkItem]] = {}
        for item in candidates:
            by_scope.setdefault(item.scope_id, []).append(item)

        for scope_id in sorted(by_scope):
            if len(waves) >= min(max_teams, max_waves if max_waves is not None else max_teams):
                break
            scope = scope_by_id.get(scope_id) or scope_by_id.get("root")
            if scope is None:
                continue
            selected: List[WorkItem] = []
            scope_conflicts: Set[str] = set()
            scope_serial_groups: Set[str] = set()

            for item in sorted(by_scope[scope_id], key=lambda value: value.id):
                if len(selected) >= max_items:
                    break
                item_conflicts = set(self._conflict_keys(item, scope))
                if item_conflicts & occupied_conflicts:
                    continue
                if item_conflicts & scope_conflicts:
                    continue
                if item.serial_group and item.serial_group in occupied_serial_groups:
                    continue
                if item.serial_group and item.serial_group in scope_serial_groups:
                    continue
                if item.execution_mode == "serial" and selected:
                    continue
                if item.kind == "ops" and selected:
                    continue

                selected.append(item)
                scope_conflicts.update(item_conflicts)
                if item.serial_group:
                    scope_serial_groups.add(item.serial_group)
                if item.execution_mode == "serial" or item.kind == "ops":
                    break

            if not selected:
                continue

            occupied_conflicts.update(scope_conflicts)
            occupied_serial_groups.update(scope_serial_groups)
            waves.append(
                TeamWave(
                    run_id=run_id,
                    scope=scope,
                    items=selected,
                    execution_plane=self._execution_plane_for(scope, selected, contract),
                    profiles=sorted({item.owner_profile for item in selected}),
                    parallel_slots=1 if self._is_serial_wave(selected) else min(max_items, len(selected)),
                    promotion_barrier=any(item.kind == "coding" for item in selected),
                    conflict_keys=sorted(scope_conflicts),
                    wave_kind=wave_kind,
                    parallel_reason=self._parallel_reason(selected, scope, wave_kind),
                    serial_reason=self._serial_reason(selected, wave_kind),
                )
            )

        return waves

    def _execution_plane_for(
        self,
        scope: WorkScope,
        items: List[WorkItem],
        contract: ContractSpec,
    ) -> str:
        team_plane = str((scope.team_policy or {}).get("workspace_plane", "")).strip()
        if team_plane:
            return team_plane
        policy = scope.execution_plane_policy
        if policy and policy != "auto":
            return policy
        if any(item.kind == "coding" for item in items):
            return "worktree"
        if all(item.kind == "research" for item in items):
            return "read-only"
        if any(item.kind == "ops" for item in items):
            return "approval-required"
        return str(
            self.runtime_overrides.get(
                "default_execution_plane",
                contract.execution_policy.get("default_execution_plane", "sandbox"),
            )
            or "sandbox"
        )

    def _is_serial_wave(self, items: List[WorkItem]) -> bool:
        if len(items) <= 1:
            return True
        return any(item.serial_group or item.execution_mode == "serial" or item.kind == "ops" for item in items)

    def _parallel_reason(self, items: List[WorkItem], scope: WorkScope, wave_kind: str) -> str:
        if len(items) <= 1:
            return ""
        conflicts = sorted({key for item in items for key in self._conflict_keys(item, scope)})
        return (
            f"{wave_kind} wave can run {len(items)} item(s) in parallel inside scope `{scope.id}` "
            f"because dependencies are satisfied and conflict keys do not overlap: {', '.join(conflicts) or 'none'}."
        )

    @staticmethod
    def _serial_reason(items: List[WorkItem], wave_kind: str) -> str:
        if len(items) > 1 and not any(item.serial_group or item.execution_mode == "serial" or item.kind == "ops" for item in items):
            return ""
        if not items:
            return ""
        item = items[0]
        if item.kind == "ops":
            return "Ops work is serialized by risk policy."
        if item.serial_group:
            return f"Serial group `{item.serial_group}` requires one-at-a-time execution."
        if item.execution_mode == "serial":
            return "WorkItem execution_mode is serial."
        if len(items) <= 1:
            return f"Only one {wave_kind} item is ready for this scope."
        return ""

    def _dependencies_satisfied_for_implementation(
        self,
        item: WorkItem,
        item_by_id: Dict[str, WorkItem],
        status_by_id: Dict[str, str],
    ) -> bool:
        return all(
            self._dependency_satisfied_for_implementation(item, dependency, item_by_id, status_by_id)
            for dependency in item.depends_on
        )

    def _dependency_satisfied_for_implementation(
        self,
        item: WorkItem,
        dependency: str,
        item_by_id: Dict[str, WorkItem],
        status_by_id: Dict[str, str],
    ) -> bool:
        status = status_by_id.get(dependency, "")
        if item.dependency_policy == "verified":
            return status == "VERIFIED"
        if item.dependency_policy == "interface":
            dependency_item = item_by_id.get(dependency)
            if dependency_item and dependency_item.provided_interfaces:
                return True
        return status in COMPLETED_STATUSES

    def _conflict_keys(self, item: WorkItem, scope: WorkScope) -> List[str]:
        if item.conflict_keys:
            return item.conflict_keys
        if item.target_artifacts:
            return [f"artifact:{artifact}" for artifact in item.target_artifacts]
        if scope.conflict_keys:
            return scope.conflict_keys
        return [f"item:{item.id}"]

    def _stateful_item(self, contract_item: WorkItem, state_item: Optional[WorkItem]) -> WorkItem:
        if state_item is None:
            return contract_item
        payload = contract_item.to_record()
        payload["status"] = state_item.status
        payload["evidence"] = state_item.evidence
        payload["inputs"] = {
            **dict(payload.get("inputs", {})),
            **dict(state_item.inputs or {}),
        }
        payload["context_policy"] = {
            **dict(payload.get("context_policy", {})),
            **dict(state_item.context_policy or {}),
        }
        payload["recovery_policy"] = {
            **dict(payload.get("recovery_policy", {})),
            **dict(state_item.recovery_policy or {}),
        }
        return WorkItem.from_mapping(payload)

    def _require_contract(self, run_id: str) -> ContractSpec:
        contract = self.store.get_contract(run_id)
        if contract is None:
            raise ValueError(f"Run {run_id} does not have a compiled contract.")
        return contract
