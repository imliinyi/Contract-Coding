"""Central recovery and repair control plane for long-running runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, TYPE_CHECKING

from ContractCoding.contract.spec import ContractSpec, WorkScope
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.quality.diagnostics import DiagnosticBuilder, DiagnosticRecord
from ContractCoding.quality.failure_router import (
    RECOVER_IMPLEMENTATION,
    RECOVER_INFRA,
    RECOVER_REPLAN,
    RECOVER_SCALE,
    RECOVER_TEST,
    FailureRouter,
)
from ContractCoding.quality.owner import OwnerResolver
from ContractCoding.runtime.health import (
    FAILURE_CONTRACT_PLAN,
    FAILURE_HUMAN_REQUIRED,
    FAILURE_INFRA,
    FAILURE_ITEM_QUALITY,
)

if TYPE_CHECKING:
    from ContractCoding.runtime.engine import AutoRunResult, RunEngine


@dataclass(frozen=True)
class ReviewBundle:
    run_id: str
    source_id: str
    source_kind: str
    scope_id: str = ""
    diagnostics: List[DiagnosticRecord] = field(default_factory=list)
    candidate_items: List[WorkItem] = field(default_factory=list)
    repeat_count: int = 0

    @property
    def actionable(self) -> List[DiagnosticRecord]:
        return [diagnostic for diagnostic in self.diagnostics if diagnostic.is_actionable()]


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str
    source_id: str
    owner_scope: str = ""
    owner_artifacts: List[str] = field(default_factory=list)
    affected_scopes: List[str] = field(default_factory=list)
    blocked_reason: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "source_id": self.source_id,
            "owner_scope": self.owner_scope,
            "owner_artifacts": list(self.owner_artifacts),
            "affected_scopes": list(self.affected_scopes),
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class RepairPlan:
    ticket_id: str = ""
    source_id: str = ""
    lane: str = ""
    owner_scope: str = ""
    owner_artifacts: List[str] = field(default_factory=list)
    affected_scopes: List[str] = field(default_factory=list)
    reopened_items: List[str] = field(default_factory=list)
    repair_packet: Dict[str, Any] = field(default_factory=dict)
    decision: RecoveryDecision | None = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "source_id": self.source_id,
            "lane": self.lane,
            "owner_scope": self.owner_scope,
            "owner_artifacts": list(self.owner_artifacts),
            "affected_scopes": list(self.affected_scopes),
            "reopened_items": list(self.reopened_items),
            "repair_packet": dict(self.repair_packet),
            "decision": self.decision.to_record() if self.decision else {},
        }


class RecoveryCoordinator:
    """Global review and repair coordinator.

    The coordinator sees run-level evidence, chooses the recovery lane, opens
    auditable repair tickets, and assigns repair to the correct lane. Team gate
    repair stays owner-local; final gate implementation repair is centralized in
    a dedicated convergence item so no worker shard has to reason about global
    failures from a partial artifact view.
    """

    def __init__(self, engine: "RunEngine"):
        self.engine = engine
        self.failure_router = FailureRouter()
        self.owner_resolver = OwnerResolver()

    FINAL_REPAIR_SCOPE_ID = "final_repair"
    FINAL_REPAIR_ITEM_ID = "final_repair:convergence"
    MAX_FINAL_CONVERGENCE_ARTIFACTS = 24

    def recover_without_replan(
        self,
        run_id: str,
        status: Dict[str, Any],
        guardrails: Dict[str, int],
    ) -> bool:
        return self._recover_without_replan(run_id, status, guardrails)

    def _decision_for_gate(self, bundle: ReviewBundle) -> RecoveryDecision:
        route = self.failure_router.classify_diagnostics(bundle.diagnostics)
        owner_scope = ""
        owner_artifacts: List[str] = []
        affected_scopes: List[str] = []
        actionable = bundle.actionable or bundle.diagnostics
        if actionable:
            owner_scope = next((diagnostic.primary_scope for diagnostic in actionable if diagnostic.primary_scope), "")
            owner_artifacts = self._owner_artifacts_for_diagnostics(
                bundle.candidate_items,
                actionable,
                repeat_count=bundle.repeat_count,
            )
            affected_scopes = self._ordered_repair_scopes_for_diagnostics(
                actionable,
                is_final=bundle.scope_id in {"integration", "final"} or bundle.source_id == "final",
                route_action=route.action,
                repeat_count=bundle.repeat_count,
            )
        return RecoveryDecision(
            action=route.action,
            reason=route.reason,
            source_id=bundle.source_id,
            owner_scope=owner_scope,
            owner_artifacts=owner_artifacts,
            affected_scopes=affected_scopes,
        )

    def _recover_without_replan(
        self,
        run_id: str,
        status: Dict[str, Any],
        guardrails: Dict[str, int],
    ) -> bool:
        blocked_items = [item for item in status.get("work_items", []) if item.status == "BLOCKED"]
        # Recovery decisions are owned by the control plane. A worker-level
        # report_blocker is evidence, not permission to reopen a different
        # WorkItem. Cross-scope repair is handled by gate/final diagnostics.
        gate_recovery = self._recover_failed_gates(run_id, guardrails)
        if gate_recovery:
            return True
        if not blocked_items:
            return False

        diagnostics = status.get("health").diagnostics if status.get("health") else []
        kind_by_item: Dict[str, str] = {}
        for diagnostic in diagnostics:
            if diagnostic.code != "work_item_blocked":
                continue
            for item in blocked_items:
                if item.id in diagnostic.message:
                    kind_by_item[item.id] = diagnostic.failure_kind or FAILURE_ITEM_QUALITY

        recovered_any = False
        for item in blocked_items:
            if self._recover_final_repair_blocker(run_id, item):
                recovered_any = True
                continue
            failure_kind = kind_by_item.get(item.id, self._failure_kind_from_item(run_id, item.id))
            item_diagnostics: List[DiagnosticRecord] = []
            if failure_kind == FAILURE_ITEM_QUALITY:
                item_diagnostics = DiagnosticBuilder.from_gate_failure(
                    gate_id=f"item:{item.id}",
                    scope_id=item.scope_id,
                    errors=[*item.evidence[-5:], "Automatic item repair requested from validation evidence."],
                    affected_artifacts=item.target_artifacts,
                )
            repair_counter_id = self._item_repair_counter_id(item, failure_kind, item_diagnostics)
            if failure_kind == FAILURE_INFRA:
                limit_key = "infra_retry_limit"
                counter_key = "infra_retries"
                event_type = "infra_retry"
                evidence = "Automatic infra retry requested; contract replan not consumed."
            elif failure_kind == FAILURE_ITEM_QUALITY:
                limit_key = "test_repair_limit" if self._work_item_targets_tests(item) else "item_repair_limit"
                counter_key = "item_repairs"
                event_type = "item_repair_requested"
                evidence = "Automatic item repair requested from validation evidence; contract replan not consumed."
            elif failure_kind == FAILURE_CONTRACT_PLAN:
                continue
            else:
                self.engine.store.append_event(
                    run_id,
                    "needs_human",
                    {
                        "work_item_id": item.id,
                        "failure_kind": failure_kind or FAILURE_HUMAN_REQUIRED,
                        "reason": "Recovery requires human approval or a policy decision.",
                    },
                )
                continue

            ticket = self._open_item_repair_ticket(
                run_id,
                item,
                failure_kind=failure_kind,
                diagnostics=item_diagnostics,
                repair_instruction=evidence,
                counter_id=repair_counter_id,
            )
            prior_repairs = self._counter_value(run_id, counter_key, repair_counter_id)
            if prior_repairs >= self._repair_limit_for_item(item, guardrails, limit_key):
                self.engine.store.update_repair_ticket_status(
                    ticket.id,
                    "BLOCKED",
                    metadata={"limit": limit_key, "counter_id": repair_counter_id},
                )
                self.engine.store.append_event(
                    run_id,
                    "automatic_recovery_limit_reached",
                    {
                        "work_item_id": item.id,
                        "counter_id": repair_counter_id,
                        "failure_kind": failure_kind,
                        "limit": limit_key,
                    },
                )
                continue

            self._increment_counter(run_id, counter_key, repair_counter_id)
            if repair_counter_id != item.id:
                self._increment_item_counter(run_id, counter_key, item.id)
            ticket = self.engine.store.increment_repair_ticket_attempt(
                ticket.id,
                metadata={"counter_id": repair_counter_id, "failure_kind": failure_kind},
            )
            repair_mode = ""
            if item_diagnostics and prior_repairs >= 1 and any(
                diagnostic.failure_kind == "syntax_error" for diagnostic in item_diagnostics
            ):
                repair_mode = "rewrite_enclosing_function_or_class"
            if item_diagnostics:
                self._force_work_item_status(
                    run_id,
                    item,
                    "READY",
                    evidence,
                    diagnostics=item_diagnostics,
                    repair_mode=repair_mode,
                    repair_ticket_id=ticket.id,
                )
            else:
                self.engine.store.update_work_item_status(run_id, item.id, "READY", evidence=[evidence])
            self.engine.store.append_event(
                run_id,
                event_type,
                {
                    "work_item_id": item.id,
                    "counter_id": repair_counter_id,
                    "failure_kind": failure_kind,
                    "limit": limit_key,
                    "diagnostics": [diagnostic.to_record() for diagnostic in item_diagnostics[:3]],
                    "repair_mode": repair_mode,
                    "repair_ticket_id": ticket.id,
                    "repair_lane": ticket.lane,
                },
            )
            self.engine.store.update_run_status(run_id, "RUNNING")
            recovered_any = True
        return recovered_any

    def _recover_failed_gates(self, run_id: str, guardrails: Dict[str, int]) -> bool:
        failed_gates = [
            gate
            for gate in self.engine.store.list_gates(run_id)
            if gate.status in {"FAILED", "BLOCKED"}
        ]
        if not failed_gates:
            return False
        items = self.engine.store.list_work_items(run_id)
        recovered_any = False
        for gate in failed_gates:
            diagnostics = self._diagnostics_for_gate(gate)
            if not diagnostics:
                self.engine.store.update_gate_status(
                    run_id,
                    gate.gate_id,
                    "BLOCKED",
                    evidence=[
                        "Structured diagnostic is required before repair; blind repair is disabled."
                    ],
                    metadata={"diagnostic_blocked": True},
                )
                team = self.engine.store.get_scope_team_run(run_id, gate.scope_id)
                if team:
                    self.engine.store.update_team_run_status(
                        team.id,
                        "BLOCKED",
                        {"diagnostic_blocked": True},
                    )
                self.engine.store.append_event(
                    run_id,
                    "diagnostic_blocked",
                    {"gate_id": gate.gate_id, "scope_id": gate.scope_id},
                )
                continue
            if self._recover_system_artifact_failure(run_id, gate, diagnostics):
                recovered_any = True
                continue
            route = self.failure_router.classify_diagnostics(diagnostics)
            decision = self._decision_for_gate(
                ReviewBundle(
                    run_id=run_id,
                    source_id=gate.gate_id,
                    source_kind=gate.gate_type or "gate",
                    scope_id=gate.scope_id,
                    diagnostics=diagnostics,
                    candidate_items=items,
                )
            )
            repair_limit_key = "item_repair_limit" if route.action in {RECOVER_IMPLEMENTATION, RECOVER_SCALE} else "test_repair_limit"
            repair_limit_default = (
                self.engine.config.AUTO_ITEM_REPAIR_MAX
                if route.action in {RECOVER_IMPLEMENTATION, RECOVER_SCALE}
                else self.engine.config.AUTO_TEST_REPAIR_MAX
            )
            counter_id = self._gate_repair_counter_id(gate.gate_id, route.action, diagnostics)
            ticket = self._open_gate_repair_ticket(
                run_id,
                gate,
                route_action=route.action,
                route_reason=route.reason,
                diagnostics=diagnostics,
            )
            if self._counter_value(run_id, "gate_repairs", counter_id) >= int(
                guardrails.get(repair_limit_key, repair_limit_default)
            ):
                self.engine.store.update_repair_ticket_status(
                    ticket.id,
                    "BLOCKED",
                    metadata={"limit": repair_limit_key, "counter_id": counter_id},
                )
                self.engine.store.append_event(
                    run_id,
                    "automatic_recovery_limit_reached",
                    {
                        "gate_id": gate.gate_id,
                        "counter_id": counter_id,
                        "failure_kind": route.action,
                        "limit": repair_limit_key,
                    },
                )
                continue
            if route.action == RECOVER_REPLAN:
                self.engine.store.update_repair_ticket_status(
                    ticket.id,
                    "OPEN",
                    metadata={
                        "impact_replan_required": True,
                        "counter_id": counter_id,
                        "route": route.__dict__,
                    },
                )
                self.engine.store.append_event(
                    run_id,
                    "impact_replan_ticket_opened",
                    {
                        "repair_ticket_id": ticket.id,
                        "gate_id": gate.gate_id,
                        "scope_id": gate.scope_id,
                        "reason": route.reason,
                    },
                )
                continue
            reopened: List[str] = []
            repeat_count_for_event: Optional[int] = None
            if route.action in {RECOVER_IMPLEMENTATION, RECOVER_SCALE}:
                actionable = [diagnostic for diagnostic in diagnostics if diagnostic.is_actionable()]
                if not actionable:
                    self.engine.store.update_repair_ticket_status(
                        ticket.id,
                        "BLOCKED",
                        metadata={"diagnostic_blocked": True, "route": route.__dict__},
                    )
                    self.engine.store.update_gate_status(
                        run_id,
                        gate.gate_id,
                        "BLOCKED",
                        evidence=[
                            "Structured diagnostic is required before implementation repair; blind repair is disabled."
                        ],
                        metadata={"failure_route": route.__dict__},
                    )
                    team = self.engine.store.get_scope_team_run(run_id, gate.scope_id)
                    if team:
                        self.engine.store.update_team_run_status(
                            team.id,
                            "BLOCKED",
                            {"diagnostic_blocked": True, "failure_route": route.__dict__},
                        )
                    self.engine.store.append_event(
                        run_id,
                        "diagnostic_blocked",
                        {"gate_id": gate.gate_id, "scope_id": gate.scope_id, "route": route.__dict__},
                    )
                    continue
                repeat_count = self._increment_diagnostic_fingerprints(run_id, actionable)
                repeat_count_for_event = repeat_count + 1
                if gate.gate_type == "final" and route.action != RECOVER_SCALE and repeat_count >= 2:
                    self.engine.store.update_repair_ticket_status(
                        ticket.id,
                        "BLOCKED",
                        metadata={
                            "route": route.__dict__,
                            "repeat_count": repeat_count + 1,
                            "diagnostics": [d.to_record() for d in actionable],
                        },
                    )
                    self.engine.store.update_gate_status(
                        run_id,
                        gate.gate_id,
                        "BLOCKED",
                        evidence=[
                            "Repeated final diagnostic reached the convergence stop; blind broad repair is disabled.",
                            *[diagnostic.summary() for diagnostic in actionable[:3]],
                        ],
                        metadata={"failure_route": route.__dict__, "diagnostics": [d.to_record() for d in actionable]},
                    )
                    self.engine.store.append_event(
                        run_id,
                        "diagnostic_blocked",
                        {
                            "gate_id": gate.gate_id,
                            "scope_id": gate.scope_id,
                            "route": route.__dict__,
                            "repeat_count": repeat_count + 1,
                        },
                    )
                    continue
                if gate.gate_type == "final" or gate.gate_id == "final":
                    final_plan = self._request_final_convergence_repair(
                        run_id=run_id,
                        gate=gate,
                        route=route,
                        diagnostics=actionable,
                        ticket=ticket,
                        counter_id=counter_id,
                        repeat_count=repeat_count,
                        decision=decision,
                    )
                    if final_plan is None:
                        continue
                    self.engine.store.update_run_status(run_id, "RUNNING")
                    recovered_any = True
                    continue
                repair_items = self._items_for_diagnostics(
                    items,
                    gate.scope_id,
                    actionable,
                    gate_id=gate.gate_id,
                    route_action=route.action,
                    repeat_count=repeat_count,
                )
                if not repair_items:
                    self.engine.store.update_repair_ticket_status(
                        ticket.id,
                        "BLOCKED",
                        metadata={"route": route.__dict__, "diagnostics": [d.to_record() for d in actionable]},
                    )
                    self.engine.store.update_gate_status(
                        run_id,
                        gate.gate_id,
                        "BLOCKED",
                        evidence=[
                            "Structured diagnostic did not identify an owned implementation artifact; blind repair is disabled.",
                            *[diagnostic.summary() for diagnostic in actionable[:3]],
                        ],
                        metadata={"failure_route": route.__dict__, "diagnostics": [d.to_record() for d in actionable]},
                    )
                    self.engine.store.append_event(
                        run_id,
                        "diagnostic_blocked",
                        {"gate_id": gate.gate_id, "scope_id": gate.scope_id, "route": route.__dict__},
                    )
                    continue
                ticket = self._open_gate_repair_ticket(
                    run_id,
                    gate,
                    route_action=route.action,
                    route_reason=route.reason,
                    diagnostics=actionable,
                    repair_items=repair_items,
                )
                self._increment_counter(run_id, "gate_repairs", counter_id)
                ticket = self.engine.store.increment_repair_ticket_attempt(
                    ticket.id,
                    metadata={"counter_id": counter_id, "route": route.__dict__},
                )
                if repeat_count >= 1:
                    self.engine.store.append_event(
                        run_id,
                        "focused_repair_requested",
                        {
                            "gate_id": gate.gate_id,
                            "scope_id": gate.scope_id,
                            "repeat_count": repeat_count + 1,
                            "fingerprints": [diagnostic.fingerprint() for diagnostic in actionable],
                        },
                    )
                for item in repair_items:
                    if item.id.startswith("interface:"):
                        continue
                    self._force_work_item_status(
                        run_id,
                        item,
                        "READY",
                        (
                            f"Gate `{gate.gate_id}` requested {route.action}; "
                            "repair using the structured diagnostic."
                        ),
                        diagnostics=actionable,
                        repair_ticket_id=ticket.id,
                    )
                    reopened.append(item.id)
                affected_scopes = sorted({item.scope_id for item in repair_items if item.scope_id})
                self.engine.store.update_gate_status(
                    run_id,
                    gate.gate_id,
                    "PENDING",
                    evidence=[
                        f"Waiting for implementation repair: {', '.join(reopened)}",
                        *([f"Focused repair for repeated diagnostic attempt {repeat_count + 1}."] if repeat_count >= 1 else []),
                        *[diagnostic.summary() for diagnostic in actionable[:3]],
                    ],
                    metadata={"latest_diagnostics": [diagnostic.to_record() for diagnostic in actionable]},
                )
                for affected_scope in affected_scopes or [gate.scope_id]:
                    if affected_scope == "integration":
                        continue
                    if gate.gate_type == "final":
                        self.engine.store.update_gate_status(
                            run_id,
                            f"team:{affected_scope}",
                            "PENDING",
                            evidence=[
                                f"Team gate requires revalidation after final gate diagnostic `{gate.gate_id}`.",
                                *[diagnostic.summary() for diagnostic in actionable[:2]],
                            ],
                            metadata={
                                "stale_by_final_gate": gate.gate_id,
                                "latest_diagnostics": [diagnostic.to_record() for diagnostic in actionable],
                            },
                        )
                    team = self.engine.store.get_scope_team_run(run_id, affected_scope)
                    if team:
                        self.engine.store.update_team_run_status(
                            team.id,
                            "REPAIRING",
                            {
                                "failure_route": route.__dict__,
                                "latest_diagnostics": [diagnostic.to_record() for diagnostic in actionable],
                                "reopened_by_gate": gate.gate_id,
                            },
                        )
            elif route.action in {RECOVER_TEST, RECOVER_INFRA}:
                if route.action == RECOVER_TEST and gate.gate_type == "final":
                    test_diagnostics = [diagnostic for diagnostic in diagnostics if diagnostic.fingerprint()]
                    repeat_count = self._increment_diagnostic_fingerprints(run_id, test_diagnostics)
                    repeat_count_for_event = repeat_count + 1
                    if repeat_count >= 2:
                        self.engine.store.update_repair_ticket_status(
                            ticket.id,
                            "BLOCKED",
                            metadata={
                                "route": route.__dict__,
                                "repeat_count": repeat_count + 1,
                                "diagnostics": [diagnostic.to_record() for diagnostic in test_diagnostics],
                            },
                        )
                        self.engine.store.update_gate_status(
                            run_id,
                            gate.gate_id,
                            "BLOCKED",
                            evidence=[
                                "Repeated final test-regeneration diagnostic reached the convergence stop; "
                                "blind test rewriting is disabled.",
                                *[diagnostic.summary() for diagnostic in test_diagnostics[:3]],
                            ],
                            metadata={
                                "failure_route": route.__dict__,
                                "diagnostics": [diagnostic.to_record() for diagnostic in test_diagnostics],
                            },
                        )
                        self.engine.store.append_event(
                            run_id,
                            "diagnostic_blocked",
                            {
                                "gate_id": gate.gate_id,
                                "scope_id": gate.scope_id,
                                "route": route.__dict__,
                                "repeat_count": repeat_count + 1,
                            },
                        )
                        continue
                self._increment_counter(run_id, "gate_repairs", counter_id)
                ticket = self.engine.store.increment_repair_ticket_attempt(
                    ticket.id,
                    metadata={"counter_id": counter_id, "route": route.__dict__},
                )
                metadata = {"failure_route": route.__dict__}
                if route.action == RECOVER_TEST:
                    metadata["allow_test_repair"] = True
                    metadata["target_test_artifacts"] = self._test_artifacts_for_diagnostics(diagnostics)
                metadata["repair_ticket_id"] = ticket.id
                self.engine.store.update_gate_status(
                    run_id,
                    gate.gate_id,
                    "PENDING",
                    evidence=[f"Retrying gate after {route.action}: {route.reason}"],
                    metadata=metadata,
                )
            else:
                continue
            self.engine.store.append_event(
                run_id,
                "gate_repair_requested",
                {
                    "gate_id": gate.gate_id,
                    "scope_id": gate.scope_id,
                    "route": route.__dict__,
                    "recovery_decision": decision.to_record(),
                    "repair_plan": RepairPlan(
                        ticket_id=ticket.id,
                        source_id=gate.gate_id,
                        lane=ticket.lane,
                        owner_scope=ticket.owner_scope,
                        owner_artifacts=ticket.owner_artifacts,
                        affected_scopes=ticket.affected_scopes,
                        reopened_items=reopened,
                        repair_packet={
                            "protocol_version": "2",
                            "repair_ticket_id": ticket.id,
                            "source_gate": gate.gate_id,
                            "lane": ticket.lane,
                            "owner_scope": ticket.owner_scope,
                            "allowed_artifacts": list(ticket.owner_artifacts),
                            "reopened_items": list(reopened),
                            "repair_bundle": dict((ticket.metadata or {}).get("repair_bundle", {}) or {}),
                        },
                        decision=decision,
                    ).to_record(),
                    "reopened_items": reopened,
                    "counter_id": counter_id,
                    "repeat_count": repeat_count_for_event,
                    "repair_ticket_id": ticket.id,
                    "repair_lane": ticket.lane,
                },
            )
            self.engine.store.update_run_status(run_id, "RUNNING")
            recovered_any = True
        return recovered_any

    def _request_final_convergence_repair(
        self,
        *,
        run_id: str,
        gate,
        route,
        diagnostics: List[DiagnosticRecord],
        ticket,
        counter_id: str,
        repeat_count: int,
        decision: RecoveryDecision,
    ) -> Optional[RepairPlan]:
        contract = self.engine.store.get_contract(run_id)
        if contract is None:
            self.engine.store.update_repair_ticket_status(
                ticket.id,
                "BLOCKED",
                metadata={"reason": "missing_contract_for_final_repair", "route": route.__dict__},
            )
            return None
        items = self.engine.store.list_work_items(run_id)
        owner_artifacts = self._final_convergence_artifacts(contract, items, diagnostics)
        if not owner_artifacts:
            self.engine.store.update_repair_ticket_status(
                ticket.id,
                "BLOCKED",
                metadata={"reason": "no_final_repair_artifacts", "route": route.__dict__},
            )
            self.engine.store.update_gate_status(
                run_id,
                gate.gate_id,
                "BLOCKED",
                evidence=[
                    "Final gate diagnostic did not identify implementation artifacts for centralized repair.",
                    *[diagnostic.summary() for diagnostic in diagnostics[:3]],
                ],
                metadata={"failure_route": route.__dict__, "diagnostics": [d.to_record() for d in diagnostics]},
            )
            self.engine.store.append_event(
                run_id,
                "diagnostic_blocked",
                {"gate_id": gate.gate_id, "scope_id": gate.scope_id, "route": route.__dict__},
            )
            return None

        affected_scopes = self._final_convergence_affected_scopes(diagnostics, owner_artifacts, items)
        repair_bundle = self._central_final_repair_bundle(
            gate=gate,
            diagnostics=diagnostics,
            owner_artifacts=owner_artifacts,
            affected_scopes=affected_scopes,
        )
        repair_instruction = self._central_final_repair_instruction(diagnostics, owner_artifacts)
        preliminary_ticket_id = ticket.id
        ticket = self._open_central_final_repair_ticket(
            run_id=run_id,
            gate=gate,
            route_action=route.action,
            route_reason=route.reason,
            diagnostics=diagnostics,
            owner_artifacts=owner_artifacts,
            affected_scopes=affected_scopes,
            repair_instruction=repair_instruction,
            repair_bundle=repair_bundle,
        )
        if preliminary_ticket_id != ticket.id:
            self.engine.store.update_repair_ticket_status(
                preliminary_ticket_id,
                "SUPERSEDED",
                metadata={
                    "superseded_by": ticket.id,
                    "reason": "final implementation repair is centralized",
                },
            )
        self._increment_counter(run_id, "gate_repairs", counter_id)
        ticket = self.engine.store.increment_repair_ticket_attempt(
            ticket.id,
            metadata={
                "counter_id": counter_id,
                "route": route.__dict__,
                "centralized_final_repair": True,
                "repeat_count": repeat_count + 1,
            },
        )
        repair_item = self._ensure_final_convergence_work_item(
            run_id=run_id,
            contract=contract,
            ticket_id=ticket.id,
            owner_artifacts=owner_artifacts,
            diagnostics=diagnostics,
            repair_instruction=repair_instruction,
            repair_bundle=repair_bundle,
        )
        reopened = [repair_item.id]
        self.engine.store.update_gate_status(
            run_id,
            gate.gate_id,
            "PENDING",
            evidence=[
                f"Waiting for centralized final convergence repair: {repair_item.id}",
                *([f"Focused repair for repeated diagnostic attempt {repeat_count + 1}."] if repeat_count >= 1 else []),
                *[diagnostic.summary() for diagnostic in diagnostics[:3]],
            ],
            metadata={
                "latest_diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                "repair_ticket_id": ticket.id,
                "centralized_final_repair": True,
                "centralized_repair_item": repair_item.id,
                "centralized_repair_artifacts": owner_artifacts,
                "failure_route": route.__dict__,
            },
        )
        plan = RepairPlan(
            ticket_id=ticket.id,
            source_id=gate.gate_id,
            lane=ticket.lane,
            owner_scope=ticket.owner_scope,
            owner_artifacts=ticket.owner_artifacts,
            affected_scopes=ticket.affected_scopes,
            reopened_items=reopened,
            repair_packet={
                "protocol_version": "2",
                "repair_ticket_id": ticket.id,
                "source_gate": gate.gate_id,
                "lane": ticket.lane,
                "owner_scope": ticket.owner_scope,
                "allowed_artifacts": list(owner_artifacts),
                "reopened_items": reopened,
                "repair_bundle": repair_bundle,
                "centralized_final_repair": True,
            },
            decision=decision,
        )
        self.engine.store.append_event(
            run_id,
            "gate_repair_requested",
            {
                "gate_id": gate.gate_id,
                "scope_id": gate.scope_id,
                "route": route.__dict__,
                "recovery_decision": decision.to_record(),
                "repair_plan": plan.to_record(),
                "reopened_items": reopened,
                "counter_id": counter_id,
                "repeat_count": repeat_count + 1,
                "repair_ticket_id": ticket.id,
                "repair_lane": ticket.lane,
                "centralized_final_repair": True,
            },
        )
        self.engine.store.append_event(
            run_id,
            "centralized_final_repair_requested",
            {
                "repair_ticket_id": ticket.id,
                "work_item_id": repair_item.id,
                "owner_artifacts": owner_artifacts,
                "affected_scopes": affected_scopes,
                "diagnostic_fingerprints": [diagnostic.fingerprint() for diagnostic in diagnostics],
            },
        )
        return plan

    def _recover_final_repair_blocker(self, run_id: str, item: WorkItem) -> bool:
        diagnostics = DiagnosticBuilder.from_records(item.inputs.get("diagnostics", []))
        if not diagnostics or not any(diagnostic.gate_id == "final" for diagnostic in diagnostics):
            return False
        repair_ticket_id = str(item.inputs.get("repair_ticket_id", "") or "")
        if not repair_ticket_id:
            return False
        latest = self.engine.store.latest_step_for_item(run_id, item.id)
        blocker = self._structured_blocker_from_step(latest)
        required = self._dedupe(
            self._normalize_repair_path(path)
            for path in blocker.get("required_artifacts", []) or []
            if path
        )
        required = [path for path in required if path and not self._is_test_artifact(path)]
        if not required:
            return False

        if not self._is_final_convergence_item(item):
            return False

        current = [self._normalize_repair_path(path) for path in item.target_artifacts]
        widened = self._dedupe([*current, *required])
        if widened == current:
            self._force_work_item_status(
                run_id,
                item,
                "READY",
                (
                    "Centralized final repair reported a blocker for artifacts already in its repair bundle; "
                    "retrying the same centralized repair item instead of routing to another team."
                ),
                diagnostics=diagnostics,
                repair_ticket_id=repair_ticket_id,
            )
        else:
            contract = self.engine.store.get_contract(run_id)
            if contract is None:
                return False
            repair_bundle = dict((item.inputs or {}).get("repair_bundle", {}) or {})
            repair_bundle["owner_artifacts"] = widened
            repair_bundle["bundle_widened_by_blocker"] = required
            repair_instruction = str(item.inputs.get("repair_instruction", "") or "") or self._central_final_repair_instruction(
                diagnostics,
                widened,
            )
            self._ensure_final_convergence_work_item(
                run_id=run_id,
                contract=contract,
                ticket_id=repair_ticket_id,
                owner_artifacts=widened,
                diagnostics=diagnostics,
                repair_instruction=repair_instruction,
                repair_bundle=repair_bundle,
                evidence=[
                    "Centralized final repair bundle widened after structured out-of-scope blocker: "
                    + ", ".join(required)
                ],
            )
            ticket = self.engine.store.get_repair_ticket(repair_ticket_id)
            if ticket:
                metadata = dict(ticket.metadata or {})
                metadata["repair_bundle"] = repair_bundle
                metadata["centralized_final_repair"] = True
                metadata["bundle_widened_by_blocker"] = required
                self.engine.store.ensure_repair_ticket(
                    run_id=run_id,
                    lane=ticket.lane,
                    source_gate=ticket.source_gate,
                    source_item_id=ticket.source_item_id,
                    diagnostic_fingerprint=ticket.diagnostic_fingerprint,
                    owner_scope=self.FINAL_REPAIR_SCOPE_ID,
                    owner_artifacts=widened,
                    affected_scopes=self._final_convergence_affected_scopes(diagnostics, widened, self.engine.store.list_work_items(run_id)),
                    conflict_keys=[*self._artifact_conflict_keys(widened), f"scope:{self.FINAL_REPAIR_SCOPE_ID}"],
                    failure_summary=ticket.failure_summary,
                    expected_actual=ticket.expected_actual,
                    repair_instruction=ticket.repair_instruction,
                    evidence_refs=ticket.evidence_refs,
                    metadata=metadata,
                )
        self.engine.store.update_gate_status(
            run_id,
            "final",
            "PENDING",
            evidence=[
                "Final gate is waiting for centralized convergence repair after an out-of-scope blocker.",
                *[diagnostic.summary() for diagnostic in diagnostics[:2]],
            ],
            metadata={
                "repair_ticket_id": repair_ticket_id,
                "centralized_final_repair": True,
                "centralized_repair_item": item.id,
                "centralized_repair_artifacts": widened,
                "bundle_widened_by_blocker": required,
                "latest_diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
            },
        )
        self.engine.store.append_event(
            run_id,
            "final_repair_bundle_widened",
            {
                "work_item_id": item.id,
                "required_artifacts": required,
                "owner_artifacts": widened,
                "repair_ticket_id": repair_ticket_id,
                "reason": blocker.get("reason", ""),
            },
        )
        self.engine.store.update_run_status(run_id, "RUNNING")
        return True

    @staticmethod
    def _structured_blocker_from_step(step) -> Dict[str, Any]:
        if step is None:
            return {}
        terminal = dict((step.output or {}).get("agent_terminal", {}) or {})
        if terminal.get("tool_name") == "report_blocker":
            return terminal
        explicit = (step.output or {}).get("blocker")
        if isinstance(explicit, dict):
            return explicit
        text = "\n".join(
            part
            for part in (
                step.error,
                str((step.output or {}).get("output", "") or ""),
                str((step.output or {}).get("thinking", "") or ""),
            )
            if part
        )
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                obj, _ = decoder.raw_decode(text[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and (obj.get("blocker_type") or obj.get("required_artifacts")):
                return obj
        return {}

    def _open_central_final_repair_ticket(
        self,
        *,
        run_id: str,
        gate,
        route_action: str,
        route_reason: str,
        diagnostics: List[DiagnosticRecord],
        owner_artifacts: List[str],
        affected_scopes: List[str],
        repair_instruction: str,
        repair_bundle: Dict[str, Any],
    ):
        fingerprint = self._fingerprint_for_diagnostics(
            diagnostics,
            f"{gate.gate_id}:{route_action}:central-final",
        )
        summary = "; ".join(diagnostic.summary() for diagnostic in diagnostics[:3]) or route_reason
        expected_actual = next((diagnostic.expected_actual for diagnostic in diagnostics if diagnostic.expected_actual), "")
        ticket = self.engine.store.ensure_repair_ticket(
            run_id=run_id,
            lane="integration_convergence",
            source_gate=gate.gate_id,
            diagnostic_fingerprint=fingerprint,
            owner_scope=self.FINAL_REPAIR_SCOPE_ID,
            owner_artifacts=owner_artifacts,
            affected_scopes=affected_scopes,
            conflict_keys=[*self._artifact_conflict_keys(owner_artifacts), f"scope:{self.FINAL_REPAIR_SCOPE_ID}"],
            failure_summary=summary,
            expected_actual=expected_actual,
            repair_instruction=repair_instruction,
            evidence_refs=[gate.gate_id, *[diagnostic.failing_test for diagnostic in diagnostics if diagnostic.failing_test][:3]],
            metadata={
                "route_action": route_action,
                "route_reason": route_reason,
                "gate_type": gate.gate_type,
                "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                "centralized_final_repair": True,
                "repair_bundle": repair_bundle,
            },
        )
        self._supersede_other_central_final_repair_tickets(run_id, keep_ticket_id=ticket.id)
        return ticket

    def _supersede_other_central_final_repair_tickets(self, run_id: str, *, keep_ticket_id: str) -> None:
        for ticket in self.engine.store.list_repair_tickets(run_id, statuses={"OPEN", "RUNNING"}, limit=200):
            if ticket.id == keep_ticket_id:
                continue
            if ticket.lane != "integration_convergence":
                continue
            if ticket.source_gate != "final":
                continue
            if ticket.owner_scope != self.FINAL_REPAIR_SCOPE_ID:
                continue
            self.engine.store.update_repair_ticket_status(
                ticket.id,
                "SUPERSEDED",
                metadata={"superseded_by": keep_ticket_id, "reason": "newer_final_diagnostic"},
            )

    def _ensure_final_convergence_work_item(
        self,
        *,
        run_id: str,
        contract: ContractSpec,
        ticket_id: str,
        owner_artifacts: List[str],
        diagnostics: List[DiagnosticRecord],
        repair_instruction: str,
        repair_bundle: Dict[str, Any],
        evidence: Optional[List[str]] = None,
    ) -> WorkItem:
        owner_artifacts = self._dedupe(
            self._normalize_repair_path(path)
            for path in owner_artifacts
            if path and not self._is_test_artifact(path)
        )
        locked_artifacts = self._dedupe(
            self._normalize_repair_path(path)
            for diagnostic in diagnostics
            for path in [*diagnostic.test_artifacts, *diagnostic.affected_artifacts, *diagnostic.external_artifacts]
            if path and self._is_test_artifact(path)
        )
        diagnostic_records = [diagnostic.to_record() for diagnostic in diagnostics]
        repair_packet = {
            "protocol_version": "2",
            "repair_ticket_id": ticket_id,
            "repair_mode": "centralized_final_convergence",
            "owner_scope": self.FINAL_REPAIR_SCOPE_ID,
            "allowed_artifacts": list(owner_artifacts),
            "locked_artifacts": locked_artifacts,
            "editable_tests": False,
            "diagnostic_fingerprints": [diagnostic.fingerprint() for diagnostic in diagnostics if diagnostic.fingerprint()],
            "diagnostics": diagnostic_records[:8],
            "instruction": repair_instruction,
            "centralized_final_repair": True,
            "repair_bundle": repair_bundle,
        }
        plan_item = WorkItem(
            id=self.FINAL_REPAIR_ITEM_ID,
            kind="coding",
            title="Centralized final convergence repair",
            owner_profile="Recovery_Orchestrator",
            module=self.FINAL_REPAIR_SCOPE_ID,
            depends_on=[],
            status="PENDING",
            inputs={
                "final_repair_mode": "centralized_convergence",
                "source_gate": "final",
            },
            target_artifacts=owner_artifacts,
            acceptance_criteria=[
                "Repair the final gate failure across the allowed implementation bundle.",
                "Do not edit tests unless a later final diagnostic explicitly enters test_regeneration.",
                "Keep already verified public behavior intact while making the final gate pass.",
            ],
            risk_level="high",
            scope_id=self.FINAL_REPAIR_SCOPE_ID,
            serial_group="final_convergence",
            conflict_keys=[*self._artifact_conflict_keys(owner_artifacts), f"scope:{self.FINAL_REPAIR_SCOPE_ID}"],
            execution_mode="serial",
            dependency_policy="done",
            team_policy={"team_kind": "coding", "workspace_plane": "worktree"},
            context_policy={"phase_id": "final_convergence"},
            verification_policy={"layer": "centralized_final_repair"},
            recovery_policy={"lane": "integration_convergence", "centralized": True},
            team_role_hint="centralized_final_recovery",
        )
        updated_contract = self._contract_with_final_repair_item(contract, plan_item)
        self.engine.store.save_contract_version(run_id, updated_contract)
        self.engine.team_runtime.ensure_teams(run_id, updated_contract)

        existing = self.engine.store.get_work_item(run_id, self.FINAL_REPAIR_ITEM_ID)
        state_payload = plan_item.to_record()
        state_payload["status"] = "READY"
        state_payload["inputs"] = {
            **dict(plan_item.inputs),
            "repair_ticket_id": ticket_id,
            "diagnostics": diagnostic_records,
            "latest_diagnostic": diagnostic_records[0] if diagnostic_records else {},
            "repair_instruction": repair_instruction,
            "repair_packet": repair_packet,
            "repair_bundle": repair_bundle,
            "final_repair_mode": "centralized_convergence",
            "source_gate": "final",
        }
        state_payload["evidence"] = [
            *list(existing.evidence if existing else []),
            *(evidence or ["Centralized final convergence repair opened by final gate diagnostics."]),
        ]
        state_item = WorkItem.from_mapping(state_payload)
        self.engine.store.upsert_work_item(run_id, state_item)
        self.engine.store.append_event(
            run_id,
            "work_item_status",
            {"id": state_item.id, "status": "READY", "diagnostics": diagnostic_records[:3]},
        )
        return state_item

    def _contract_with_final_repair_item(self, contract: ContractSpec, repair_item: WorkItem) -> ContractSpec:
        scopes = [scope for scope in contract.work_scopes if scope.id != self.FINAL_REPAIR_SCOPE_ID]
        scopes.append(
            WorkScope(
                id=self.FINAL_REPAIR_SCOPE_ID,
                type="code_module",
                label="Centralized final convergence repair",
                parent_scope="root",
                artifacts=list(repair_item.target_artifacts),
                conflict_keys=[*self._artifact_conflict_keys(repair_item.target_artifacts), f"scope:{self.FINAL_REPAIR_SCOPE_ID}"],
                execution_plane_policy="worktree",
                team_policy={"team_kind": "coding", "workspace_plane": "worktree"},
                promotion_policy={"mode": "after_verified_items"},
                interface_stability="stable",
            )
        )
        items = [item for item in contract.work_items if item.id != repair_item.id]
        items.append(repair_item)
        metadata = dict(contract.metadata)
        runtime_items = [
            str(value)
            for value in metadata.get("runtime_final_repair_items", [])
            if str(value).strip()
        ]
        if repair_item.id not in runtime_items:
            runtime_items.append(repair_item.id)
        metadata["runtime_final_repair_items"] = runtime_items
        runtime_scopes = [
            str(value)
            for value in metadata.get("runtime_final_repair_scopes", [])
            if str(value).strip()
        ]
        if self.FINAL_REPAIR_SCOPE_ID not in runtime_scopes:
            runtime_scopes.append(self.FINAL_REPAIR_SCOPE_ID)
        metadata["runtime_final_repair_scopes"] = runtime_scopes
        metadata["final_repair_mode"] = "centralized_convergence"
        return ContractSpec(
            goals=contract.goals,
            work_scopes=scopes,
            work_items=items,
            requirements=contract.requirements,
            architecture=contract.architecture,
            milestones=contract.milestones,
            phase_plan=contract.phase_plan,
            interfaces=contract.interfaces,
            deltas=contract.deltas,
            team_gates=[gate for gate in contract.team_gates if gate.scope_id != self.FINAL_REPAIR_SCOPE_ID],
            final_gate=contract.final_gate,
            acceptance_criteria=contract.acceptance_criteria,
            execution_policy=contract.execution_policy,
            risk_policy=contract.risk_policy,
            verification_policy=contract.verification_policy,
            test_ownership=contract.test_ownership,
            version=contract.version,
            metadata=metadata,
            owner_hints=contract.owner_hints,
        )

    def _final_convergence_artifacts(
        self,
        contract: ContractSpec,
        items: List[WorkItem],
        diagnostics: List[DiagnosticRecord],
    ) -> List[str]:
        required_impl = [
            self._normalize_repair_path(path)
            for path in (contract.final_gate.required_artifacts if contract.final_gate else [])
            if path and self._looks_like_project_repair_artifact(path) and not self._is_test_artifact(path)
        ]
        required_set = set(required_impl)
        artifacts: List[str] = []

        def add(path: str) -> None:
            normalized = self._canonical_repair_artifact(path, required_impl)
            if not normalized or self._is_test_artifact(normalized) or not self._looks_like_project_repair_artifact(normalized):
                return
            if required_set and normalized not in required_set:
                return
            if normalized not in artifacts:
                artifacts.append(normalized)

        for diagnostic in diagnostics:
            for path in self._prioritized_diagnostic_repair_artifacts(diagnostic):
                add(path)
            for path in [*diagnostic.suspected_implementation_artifacts, *diagnostic.affected_artifacts]:
                add(path)
            for path in self._module_artifacts_from_diagnostic_text(diagnostic, required_impl):
                add(path)

        requested_scopes = self._dedupe(
            scope
            for diagnostic in diagnostics
            for scope in [diagnostic.primary_scope, *diagnostic.suspected_scopes, *diagnostic.fallback_scopes]
            if scope and scope not in {"integration", "tests", self.FINAL_REPAIR_SCOPE_ID}
        )
        for scope in requested_scopes:
            for item in items:
                if item.scope_id != scope or self._work_item_targets_tests(item):
                    continue
                for target in item.target_artifacts:
                    add(target)
        if not artifacts:
            for path in required_impl:
                add(path)
        return artifacts[: self.MAX_FINAL_CONVERGENCE_ARTIFACTS]

    def _final_convergence_affected_scopes(
        self,
        diagnostics: List[DiagnosticRecord],
        owner_artifacts: List[str],
        items: List[WorkItem],
    ) -> List[str]:
        scopes = self._dedupe(
            scope
            for diagnostic in diagnostics
            for scope in [diagnostic.primary_scope, *diagnostic.suspected_scopes, *diagnostic.fallback_scopes]
            if scope and scope not in {"integration", "tests", self.FINAL_REPAIR_SCOPE_ID}
        )
        for artifact in owner_artifacts:
            for item in items:
                if self._work_item_targets_tests(item):
                    continue
                if any(
                    self._path_matches_affected(self._normalize_repair_path(target), artifact)
                    for target in item.target_artifacts
                ) and item.scope_id and item.scope_id not in scopes:
                    scopes.append(item.scope_id)
        return scopes

    def _central_final_repair_bundle(
        self,
        *,
        gate,
        diagnostics: List[DiagnosticRecord],
        owner_artifacts: List[str],
        affected_scopes: List[str],
    ) -> Dict[str, Any]:
        locked_tests = self._dedupe(
            self._normalize_repair_path(path)
            for diagnostic in diagnostics
            for path in [*diagnostic.test_artifacts, *diagnostic.affected_artifacts, *diagnostic.external_artifacts]
            if path and self._is_test_artifact(path)
        )
        return {
            "bundle_id": f"{gate.gate_id}:{','.join(sorted({diagnostic.fingerprint() for diagnostic in diagnostics})[:4])}",
            "source_gate": gate.gate_id,
            "strategy": "centralized",
            "centralized_work_item": self.FINAL_REPAIR_ITEM_ID,
            "owner_scope": self.FINAL_REPAIR_SCOPE_ID,
            "owner_artifacts": list(owner_artifacts),
            "affected_scopes": list(affected_scopes),
            "locked_artifacts": locked_tests,
            "diagnostic_fingerprints": [diagnostic.fingerprint() for diagnostic in diagnostics],
            "validation_strategy": [
                "repair the whole failing final behavior in one implementation bundle",
                "compile/import touched implementation artifacts after each patch",
                "run the failing final test or unittest discovery as pending-global validation",
                "retry the final gate after the centralized repair team promotes",
            ],
        }

    def _central_final_repair_instruction(
        self,
        diagnostics: List[DiagnosticRecord],
        owner_artifacts: List[str],
    ) -> str:
        diagnostic_summaries = "; ".join(diagnostic.summary() for diagnostic in diagnostics[:3])
        return (
            "Centralized final convergence repair: fix the final gate failure across this allowed implementation bundle "
            f"({', '.join(owner_artifacts[:12])}). Keep tests locked; do not reroute to original teams. "
            "Start with the directly failing public behavior, then repair dependent state/persistence/planning code needed "
            "for the same final scenario. Diagnostics: "
            + diagnostic_summaries
        )

    @staticmethod
    def _is_final_convergence_item(item: WorkItem) -> bool:
        return item.id == RecoveryCoordinator.FINAL_REPAIR_ITEM_ID or str(item.inputs.get("final_repair_mode", "")) == "centralized_convergence"

    @classmethod
    def _canonical_repair_artifact(cls, path: str, required: List[str]) -> str:
        normalized = cls._normalize_repair_path(path)
        if not normalized:
            return ""
        for candidate in required:
            candidate_norm = cls._normalize_repair_path(candidate)
            if cls._path_matches_affected(candidate_norm, normalized):
                return candidate_norm
        return normalized

    @classmethod
    def _module_artifacts_from_diagnostic_text(
        cls,
        diagnostic: DiagnosticRecord,
        required: List[str],
    ) -> List[str]:
        text = "\n".join(
            part
            for part in (
                diagnostic.failing_test,
                diagnostic.traceback_excerpt,
                diagnostic.expected_actual,
                diagnostic.repair_instruction,
            )
            if part
        )
        artifacts: List[str] = []
        for match in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?::[^`]*)?`", text):
            module = match.group(1)
            artifact = module.replace(".", "/") + ".py"
            canonical = cls._canonical_repair_artifact(artifact, required)
            if canonical:
                artifacts.append(canonical)
        return cls._dedupe(artifacts)

    def _recover_system_artifact_failure(
        self,
        run_id: str,
        gate,
        diagnostics: List[DiagnosticRecord],
    ) -> bool:
        if not self._is_system_artifact_failure(diagnostics):
            return False
        contract = self.engine.store.get_contract(run_id)
        run = self.engine.store.get_run(run_id)
        if contract is None or run is None:
            return False
        scope_id = gate.scope_id
        if scope_id in {"integration", "phase", "final"}:
            return False
        self.engine.team_runtime.ensure_workspace(run, contract, scope_id)
        fingerprint = self._fingerprint_for_diagnostics(diagnostics, f"{gate.gate_id}:system-sync")
        ticket = self.engine.store.ensure_repair_ticket(
            run_id=run_id,
            lane="system_sync_repair",
            source_gate=gate.gate_id,
            diagnostic_fingerprint=fingerprint,
            owner_scope=scope_id,
            owner_artifacts=self._system_artifacts_from_diagnostics(diagnostics),
            affected_scopes=[scope_id],
            conflict_keys=[f"scope:{scope_id}", "system-artifacts:.contractcoding"],
            failure_summary="System contract artifact was missing from the team workspace.",
            repair_instruction="Runtime re-synced compiler-owned .contractcoding artifacts; retry the gate.",
            evidence_refs=[gate.gate_id],
            metadata={"diagnostics": [diagnostic.to_record() for diagnostic in diagnostics]},
        )
        self.engine.store.update_repair_ticket_status(ticket.id, "RESOLVED", evidence_refs=[gate.gate_id])
        self.engine.store.update_gate_status(
            run_id,
            gate.gate_id,
            "PENDING",
            evidence=[
                "System contract artifacts were re-synced to the team workspace; retrying gate.",
                *[diagnostic.summary() for diagnostic in diagnostics[:2]],
            ],
            metadata={
                "system_sync_repair": True,
                "repair_ticket_id": ticket.id,
                "latest_diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
            },
        )
        team = self.engine.store.get_scope_team_run(run_id, scope_id)
        if team:
            self.engine.store.update_team_run_status(
                team.id,
                team.status,
                {"system_sync_repair": True, "last_system_sync_gate": gate.gate_id},
            )
        self.engine.store.append_event(
            run_id,
            "system_sync_repair",
            {"gate_id": gate.gate_id, "scope_id": scope_id, "repair_ticket_id": ticket.id},
        )
        self.engine.store.update_run_status(run_id, "RUNNING")
        return True

    @staticmethod
    def _is_system_artifact_failure(diagnostics: List[DiagnosticRecord]) -> bool:
        markers = (
            ".contractcoding/interfaces/",
            ".contractcoding/interface_tests/",
            ".contractcoding/scaffolds/",
        )
        missing_markers = (
            "missing",
            "not found",
            "no such file",
            "required artifact",
            "system artifact",
        )
        for diagnostic in diagnostics:
            text_haystack = "\n".join(
                [
                    diagnostic.traceback_excerpt,
                    diagnostic.expected_actual,
                    diagnostic.repair_instruction,
                ]
            )
            lower_text = text_haystack.lower()
            if any(marker in text_haystack for marker in markers) and any(
                marker in lower_text for marker in missing_markers
            ):
                return True
            if diagnostic.failure_kind == "missing_artifact":
                paths = [
                    *diagnostic.affected_artifacts,
                    *diagnostic.suspected_implementation_artifacts,
                    *diagnostic.external_artifacts,
                ]
                if any(
                    any(str(path).replace("\\", "/").startswith(marker) for marker in markers)
                    for path in paths
                ):
                    return True
        return False

    @staticmethod
    def _system_artifacts_from_diagnostics(diagnostics: List[DiagnosticRecord]) -> List[str]:
        artifacts: List[str] = []
        for diagnostic in diagnostics:
            for path in [*diagnostic.affected_artifacts, *diagnostic.external_artifacts]:
                normalized = str(path).replace("\\", "/")
                if normalized.startswith(".contractcoding/") and normalized not in artifacts:
                    artifacts.append(normalized)
        return artifacts

    def _diagnostics_for_gate(self, gate) -> List[DiagnosticRecord]:
        diagnostics = DiagnosticBuilder.from_records(gate.metadata.get("diagnostics", []))
        if diagnostics:
            return diagnostics
        return DiagnosticBuilder.from_gate_failure(
            gate_id=gate.gate_id,
            scope_id=gate.scope_id,
            errors=gate.evidence[-5:],
        )

    @staticmethod
    def _gate_repair_counter_id(gate_id: str, action: str, diagnostics: List[DiagnosticRecord]) -> str:
        fingerprints = sorted({diagnostic.fingerprint() for diagnostic in diagnostics if diagnostic.fingerprint()})
        if fingerprints:
            return f"{gate_id}:{action}:{','.join(fingerprints[:4])}"
        return f"{gate_id}:{action}:no-diagnostic"

    @staticmethod
    def _fingerprint_for_diagnostics(diagnostics: List[DiagnosticRecord], fallback: str) -> str:
        fingerprints = sorted({diagnostic.fingerprint() for diagnostic in diagnostics if diagnostic.fingerprint()})
        if fingerprints:
            return ",".join(fingerprints[:4])
        return fallback

    def _open_item_repair_ticket(
        self,
        run_id: str,
        item: WorkItem,
        *,
        failure_kind: str,
        diagnostics: List[DiagnosticRecord],
        repair_instruction: str,
        counter_id: str,
    ):
        fingerprint = self._fingerprint_for_diagnostics(diagnostics, counter_id or item.id)
        summary = "; ".join(diagnostic.summary() for diagnostic in diagnostics[:3]) if diagnostics else repair_instruction
        expected_actual = next((diagnostic.expected_actual for diagnostic in diagnostics if diagnostic.expected_actual), "")
        return self.engine.store.ensure_repair_ticket(
            run_id=run_id,
            lane="local_patch",
            source_gate=f"item:{item.id}",
            source_item_id=item.id,
            diagnostic_fingerprint=fingerprint,
            owner_scope=item.scope_id,
            owner_artifacts=item.target_artifacts,
            affected_scopes=[item.scope_id] if item.scope_id else [],
            conflict_keys=item.conflict_keys or self._artifact_conflict_keys(item.target_artifacts),
            failure_summary=summary,
            expected_actual=expected_actual,
            repair_instruction=repair_instruction,
            evidence_refs=[item.id, *[diagnostic.gate_id for diagnostic in diagnostics[:3]]],
            metadata={
                "failure_kind": failure_kind,
                "counter_id": counter_id,
                "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
            },
        )

    def _open_gate_repair_ticket(
        self,
        run_id: str,
        gate,
        *,
        route_action: str,
        route_reason: str,
        diagnostics: List[DiagnosticRecord],
        repair_items: Optional[List[WorkItem]] = None,
    ):
        lane = self._ticket_lane_for_gate(gate, route_action)
        repair_items = repair_items or []
        owner_scope = self._ticket_owner_scope(gate, diagnostics, repair_items)
        owner_artifacts = self._ticket_owner_artifacts(diagnostics, repair_items, route_action)
        affected_scopes = self._ticket_affected_scopes(gate, diagnostics, repair_items)
        conflict_keys = self._artifact_conflict_keys(owner_artifacts)
        if owner_scope:
            conflict_keys.append(f"scope:{owner_scope}")
        fingerprint = self._fingerprint_for_diagnostics(diagnostics, f"{gate.gate_id}:{route_action}:no-diagnostic")
        summary = "; ".join(diagnostic.summary() for diagnostic in diagnostics[:3]) or route_reason
        expected_actual = next((diagnostic.expected_actual for diagnostic in diagnostics if diagnostic.expected_actual), "")
        instruction = next((diagnostic.repair_instruction for diagnostic in diagnostics if diagnostic.repair_instruction), route_reason)
        return self.engine.store.ensure_repair_ticket(
            run_id=run_id,
            lane=lane,
            source_gate=gate.gate_id,
            diagnostic_fingerprint=fingerprint,
            owner_scope=owner_scope,
            owner_artifacts=owner_artifacts,
            affected_scopes=affected_scopes,
            conflict_keys=conflict_keys,
            failure_summary=summary,
            expected_actual=expected_actual,
            repair_instruction=instruction,
            evidence_refs=[gate.gate_id, *[diagnostic.failing_test for diagnostic in diagnostics if diagnostic.failing_test][:3]],
            metadata={
                "route_action": route_action,
                "route_reason": route_reason,
                "gate_type": gate.gate_type,
                "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics],
                "repair_bundle": self._repair_bundle_metadata(gate, diagnostics, repair_items),
            },
        )

    @staticmethod
    def _ticket_lane_for_gate(gate, route_action: str) -> str:
        if route_action == RECOVER_TEST:
            return "test_regeneration"
        if route_action == RECOVER_REPLAN:
            return "interface_delta"
        if gate.gate_type == "final" or gate.gate_id == "final":
            return "integration_convergence"
        return "team_convergence"

    @staticmethod
    def _ticket_owner_scope(gate, diagnostics: List[DiagnosticRecord], repair_items: List[WorkItem]) -> str:
        for diagnostic in diagnostics:
            if diagnostic.primary_scope and diagnostic.primary_scope != "integration":
                return diagnostic.primary_scope
        for item in repair_items:
            if item.scope_id:
                return item.scope_id
        for diagnostic in diagnostics:
            for artifact in diagnostic.suspected_implementation_artifacts:
                scope = RecoveryCoordinator._scope_for_repair_artifact(artifact)
                if scope and scope != "integration":
                    return scope
        for diagnostic in diagnostics:
            for scope in diagnostic.suspected_scopes:
                if scope and scope != "integration":
                    return scope
        return gate.scope_id if gate.scope_id not in {"integration", "final"} else ""

    MAX_FINAL_REPAIR_ITEMS = 5
    MAX_FINAL_REPAIR_SCOPES = 3

    @staticmethod
    def _scope_for_repair_artifact(path: str) -> str:
        normalized = str(path or "").replace("\\", "/").strip("/")
        pieces = [piece.lower() for piece in normalized.split("/") if piece]
        name = pieces[-1] if pieces else ""
        if name == "__init__.py":
            return "package"
        if name in {"cli.py", "main.py", "__main__.py"}:
            return "interface"
        for scope in ("package", "domain", "core", "planning", "ai", "io", "interface", "tests"):
            if scope in pieces:
                return scope
        return ""

    def _ticket_owner_artifacts(
        self,
        diagnostics: List[DiagnosticRecord],
        repair_items: List[WorkItem],
        route_action: str,
    ) -> List[str]:
        artifacts: List[str] = []
        if route_action == RECOVER_TEST:
            artifacts.extend(path for diagnostic in diagnostics for path in diagnostic.test_artifacts)
        else:
            artifacts.extend(
                path
                for diagnostic in diagnostics
                for path in self._prioritized_diagnostic_repair_artifacts(diagnostic)
            )
        if not artifacts:
            artifacts.extend(path for item in repair_items for path in item.target_artifacts)
        return [
            self._normalize_repair_path(path)
            for path in dict.fromkeys(artifacts)
            if path
        ]

    @staticmethod
    def _ticket_affected_scopes(gate, diagnostics: List[DiagnosticRecord], repair_items: List[WorkItem]) -> List[str]:
        scopes: List[str] = []
        for diagnostic in diagnostics:
            for scope in [diagnostic.primary_scope, *diagnostic.suspected_scopes, *diagnostic.fallback_scopes]:
                if scope and scope != "integration" and scope not in scopes:
                    scopes.append(scope)
        for item in repair_items:
            if item.scope_id and item.scope_id not in scopes:
                scopes.append(item.scope_id)
        if not scopes and gate.scope_id not in {"integration", "final"}:
            scopes.append(gate.scope_id)
        return scopes

    @classmethod
    def _test_artifacts_for_diagnostics(cls, diagnostics: List[DiagnosticRecord]) -> List[str]:
        artifacts: List[str] = []
        for diagnostic in diagnostics:
            for path in [
                *diagnostic.test_artifacts,
                *diagnostic.affected_artifacts,
                *diagnostic.external_artifacts,
            ]:
                normalized = cls._normalize_repair_path(path)
                if normalized and cls._is_test_artifact(normalized) and normalized not in artifacts:
                    artifacts.append(normalized)
        return artifacts

    @staticmethod
    def _artifact_conflict_keys(artifacts: Iterable[str]) -> List[str]:
        keys: List[str] = []
        for artifact in artifacts:
            text = str(artifact or "").strip()
            if text:
                keys.append(f"artifact:{text}")
        return keys

    def _item_repair_counter_id(
        self,
        item: WorkItem,
        failure_kind: str,
        diagnostics: List[DiagnosticRecord],
    ) -> str:
        if not diagnostics:
            return item.id
        resolution = self.owner_resolver.resolve(
            diagnostics[0],
            candidate_artifacts=item.target_artifacts,
        )
        owner = resolution.primary_artifact or ",".join(item.target_artifacts[:2]) or item.id
        return f"{item.id}:{failure_kind}:{owner}:{diagnostics[0].fingerprint()}"

    @staticmethod
    def _looks_like_project_repair_artifact(path: str) -> bool:
        normalized = RecoveryCoordinator._normalize_repair_path(path)
        if not normalized.endswith(".py"):
            return False
        lower = normalized.lower()
        blocked_markers = (
            "site-packages/",
            "dist-packages/",
            "lib/python",
            "frameworks/python.framework/",
            "versions/",
            "importlib/",
            "<frozen",
            "/python3.",
        )
        if any(marker in lower for marker in blocked_markers):
            return False
        if normalized.startswith(("/", "../")) or ".." in normalized.split("/"):
            return False
        return "/" in normalized or normalized.startswith("test_")

    def _items_for_diagnostics(
        self,
        items: List[WorkItem],
        scope_id: str,
        diagnostics: List[DiagnosticRecord],
        gate_id: str = "",
        route_action: str = "",
        repeat_count: int = 0,
    ) -> List[WorkItem]:
        is_final = scope_id in {"integration", "final"} or gate_id == "final" or any(
            diagnostic.gate_id == "final" for diagnostic in diagnostics
        )
        ordered_scopes = self._ordered_repair_scopes_for_diagnostics(
            diagnostics,
            is_final=is_final,
            route_action=route_action,
            repeat_count=repeat_count,
        )
        suspected_scopes = set(ordered_scopes)
        if scope_id and scope_id not in {"integration", "final"} and not suspected_scopes:
            suspected_scopes.add(scope_id)
        scoped = [
            item
            for item in items
            if not item.id.startswith("interface:")
            and not item.id.startswith("scaffold:")
            and not self._work_item_targets_tests(item)
            and (not suspected_scopes or item.scope_id in suspected_scopes)
        ]
        affected = self._owner_artifacts_for_diagnostics(items, diagnostics, repeat_count=repeat_count)
        if not affected:
            low_level_kinds = {"syntax_error", "import_error", "placeholder", "out_of_scope", "missing_artifact"}
            affected = [
                self._normalize_repair_path(path)
                for diagnostic in diagnostics
                if diagnostic.failure_kind in low_level_kinds
                for path in diagnostic.affected_artifacts
                if path and not self._is_test_artifact(path)
            ]
        if not affected:
            if suspected_scopes:
                return scoped
            return []
        matched: List[WorkItem] = []
        for item in scoped:
            targets = [self._normalize_repair_path(path) for path in item.target_artifacts]
            if any(self._path_matches_affected(target, affected_path) for target in targets for affected_path in affected):
                matched.append(item)
        if not matched and suspected_scopes:
            return self._cap_final_repair_items(scoped, diagnostics, affected) if is_final else scoped
        return self._cap_final_repair_items(matched, diagnostics, affected) if is_final else matched

    def _owner_artifacts_for_diagnostics(
        self,
        items: List[WorkItem],
        diagnostics: List[DiagnosticRecord],
        *,
        repeat_count: int,
    ) -> List[str]:
        candidates = [
            self._normalize_repair_path(path)
            for item in items
            for path in item.target_artifacts
            if path and not self._is_test_artifact(path)
        ]
        artifacts: List[str] = []
        for diagnostic in diagnostics:
            is_final = diagnostic.gate_id == "final" or diagnostic.scope_id in {"integration", "final"}
            multi_scope = len({scope for scope in diagnostic.suspected_scopes if scope and scope != "integration"}) > 1
            explicit_final_artifacts = self._prioritized_diagnostic_repair_artifacts(diagnostic)
            if is_final and explicit_final_artifacts and (multi_scope or diagnostic.primary_scope):
                artifacts.extend(explicit_final_artifacts)
                continue
            resolution = self.owner_resolver.resolve(diagnostic, candidate_artifacts=candidates)
            explicit_artifact_evidence = bool(diagnostic.suspected_implementation_artifacts) or any(
                path and not self._is_test_artifact(path)
                for path in diagnostic.affected_artifacts
            )
            suspected_scopes = {
                scope for scope in diagnostic.suspected_scopes if scope and scope not in {"integration", "final"}
            }
            broad_scope_diagnostic = len(suspected_scopes) > 1 and not explicit_artifact_evidence
            if (
                resolution.primary_artifact
                and not broad_scope_diagnostic
                and (resolution.confidence >= 0.8 or explicit_artifact_evidence)
            ):
                artifacts.append(self._normalize_repair_path(resolution.primary_artifact))
            if repeat_count >= 1 and not broad_scope_diagnostic and (resolution.confidence >= 0.8 or explicit_artifact_evidence):
                artifacts.extend(self._normalize_repair_path(path) for path in resolution.fallback_artifacts)
        if artifacts:
            return list(dict.fromkeys(artifacts))
        return [
            self._normalize_repair_path(path)
            for diagnostic in diagnostics
            for path in diagnostic.suspected_implementation_artifacts
            if path and not self._is_test_artifact(path)
        ]

    @classmethod
    def _prioritized_diagnostic_repair_artifacts(cls, diagnostic: DiagnosticRecord) -> List[str]:
        artifacts = cls._dedupe(
            cls._normalize_repair_path(path)
            for path in diagnostic.suspected_implementation_artifacts
            if path and not cls._is_test_artifact(path)
        )
        if not artifacts:
            return []
        primary_scope = diagnostic.primary_scope if diagnostic.primary_scope != "integration" else ""
        if primary_scope and primary_scope != "package":
            non_package = [
                artifact
                for artifact in artifacts
                if cls._scope_for_repair_artifact(artifact) != "package"
            ]
            if non_package:
                artifacts = non_package
        if not primary_scope:
            return artifacts
        primary = [
            artifact
            for artifact in artifacts
            if cls._scope_for_repair_artifact(artifact) == primary_scope
        ]
        rest = [artifact for artifact in artifacts if artifact not in primary]
        return cls._dedupe([*primary, *rest])

    @staticmethod
    def _ordered_repair_scopes_for_diagnostics(
        diagnostics: List[DiagnosticRecord],
        *,
        is_final: bool,
        route_action: str,
        repeat_count: int,
    ) -> List[str]:
        scopes: List[str] = []
        if not is_final:
            for diagnostic in diagnostics:
                for scope in diagnostic.suspected_scopes:
                    if scope and scope != "integration" and scope not in scopes:
                        scopes.append(scope)
            return scopes
        for diagnostic in diagnostics:
            if diagnostic.primary_scope and diagnostic.primary_scope != "integration":
                if diagnostic.primary_scope not in scopes:
                    scopes.append(diagnostic.primary_scope)
            multi_owner_final = (
                len({scope for scope in diagnostic.suspected_scopes if scope and scope != "integration"}) > 1
                or len(
                    {
                        RecoveryCoordinator._scope_for_repair_artifact(path)
                        for path in diagnostic.suspected_implementation_artifacts
                        if path
                    }
                    - {"", "integration"}
                )
                > 1
            )
            if route_action == RECOVER_SCALE or repeat_count >= 1 or multi_owner_final:
                for scope in [*diagnostic.fallback_scopes, *diagnostic.suspected_scopes]:
                    if scope and scope != "integration" and scope not in scopes:
                        scopes.append(scope)
        if not scopes:
            for diagnostic in diagnostics:
                for scope in diagnostic.suspected_scopes:
                    if scope and scope != "integration" and scope not in scopes:
                        scopes.append(scope)
        if route_action == RECOVER_IMPLEMENTATION and len(scopes) > RecoveryCoordinator.MAX_FINAL_REPAIR_SCOPES:
            return scopes[: RecoveryCoordinator.MAX_FINAL_REPAIR_SCOPES]
        return scopes

    def _cap_final_repair_items(
        self,
        repair_items: List[WorkItem],
        diagnostics: List[DiagnosticRecord],
        affected_artifacts: List[str],
    ) -> List[WorkItem]:
        ranked_artifacts = self._dedupe(
            [
                *affected_artifacts,
                *[
                    self._normalize_repair_path(path)
                    for diagnostic in diagnostics
                    for path in diagnostic.suspected_implementation_artifacts
                    if path and not self._is_test_artifact(path)
                ],
            ]
        )
        ranked_scopes = self._dedupe(
            [
                scope
                for diagnostic in diagnostics
                for scope in [diagnostic.primary_scope, *diagnostic.suspected_scopes, *diagnostic.fallback_scopes]
                if scope and scope != "integration"
            ]
        )[: self.MAX_FINAL_REPAIR_SCOPES]
        if ranked_scopes:
            scoped = [item for item in repair_items if item.scope_id in ranked_scopes]
            if scoped:
                repair_items = scoped

        def rank(item: WorkItem) -> tuple[int, int, str]:
            best = len(ranked_artifacts) + 100
            for index, artifact in enumerate(ranked_artifacts):
                if any(
                    self._path_matches_affected(self._normalize_repair_path(target), artifact)
                    for target in item.target_artifacts
                ):
                    best = index
                    break
            scope_index = ranked_scopes.index(item.scope_id) if item.scope_id in ranked_scopes else 99
            return (best, scope_index, item.id)

        return sorted(repair_items, key=rank)[: self.MAX_FINAL_REPAIR_ITEMS]

    @staticmethod
    def _repair_bundle_metadata(gate, diagnostics: List[DiagnosticRecord], repair_items: List[WorkItem]) -> Dict[str, Any]:
        shards: List[Dict[str, Any]] = []
        for item in repair_items:
            owner_artifacts = list(item.target_artifacts)
            related = [
                diagnostic
                for diagnostic in diagnostics
                if not diagnostic.suspected_implementation_artifacts
                or any(
                    RecoveryCoordinator._path_matches_affected(
                        RecoveryCoordinator._normalize_repair_path(target),
                        RecoveryCoordinator._normalize_repair_path(artifact),
                    )
                    for target in owner_artifacts
                    for artifact in diagnostic.suspected_implementation_artifacts
                )
                or item.scope_id in diagnostic.suspected_scopes
            ]
            shards.append(
                {
                    "shard_id": item.id,
                    "owner_scope": item.scope_id,
                    "owner_artifacts": owner_artifacts,
                    "diagnostic_fingerprints": [diagnostic.fingerprint() for diagnostic in related[:4]],
                    "validation_strategy": [
                        "compile/import touched artifacts",
                        "run cluster-local tests when identified",
                        "defer full final gate until all repair shards finish",
                    ],
                }
            )
        return {
            "bundle_id": f"{gate.gate_id}:{','.join(sorted({diagnostic.fingerprint() for diagnostic in diagnostics})[:4])}",
            "source_gate": gate.gate_id,
            "strategy": "multi_owner" if len({shard["owner_scope"] for shard in shards}) > 1 else "single_owner",
            "shards": shards,
        }

    def _increment_diagnostic_fingerprints(self, run_id: str, diagnostics: List[DiagnosticRecord]) -> int:
        run = self.engine.store.get_run(run_id)
        metadata = dict(run.metadata if run else {})
        counters = dict(metadata.get("diagnostic_fingerprints", {}))
        fingerprints = [diagnostic.fingerprint() for diagnostic in diagnostics]
        previous = max((int(counters.get(fingerprint, 0)) for fingerprint in fingerprints), default=0)
        for fingerprint in fingerprints:
            counters[fingerprint] = int(counters.get(fingerprint, 0)) + 1
        metadata["diagnostic_fingerprints"] = counters
        if run:
            self.engine.store.update_run_status(run_id, run.status, metadata)
        return previous

    @staticmethod
    def _normalize_repair_path(path: str) -> str:
        return os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/").strip("/")

    @staticmethod
    def _path_matches_affected(target: str, affected: str) -> bool:
        if not target or not affected:
            return False
        return target == affected or target.endswith("/" + affected) or affected.endswith("/" + target)

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    def _force_work_item_status(
        self,
        run_id: str,
        item: WorkItem,
        status: str,
        evidence: str,
        diagnostics: Optional[List[DiagnosticRecord]] = None,
        repair_mode: str = "",
        repair_ticket_id: str = "",
    ) -> None:
        payload = item.to_record()
        payload["status"] = status
        diagnostic_records = [diagnostic.to_record() for diagnostic in diagnostics or []]
        diagnostic_evidence = [diagnostic.summary() for diagnostic in diagnostics or []]
        payload["evidence"] = [*list(item.evidence), evidence, *diagnostic_evidence]
        if diagnostic_records or repair_ticket_id:
            inputs = dict(payload.get("inputs", {}))
            inputs["diagnostics"] = diagnostic_records
            if diagnostic_records:
                inputs["latest_diagnostic"] = diagnostic_records[0]
                inputs["repair_instruction"] = diagnostic_records[0].get("repair_instruction", "")
            if repair_mode:
                inputs["repair_mode"] = repair_mode
            if repair_ticket_id:
                inputs["repair_ticket_id"] = repair_ticket_id
            inputs["repair_packet"] = self._build_repair_packet(
                item,
                diagnostics or [],
                repair_ticket_id=repair_ticket_id,
                repair_mode=repair_mode,
            )
            payload["inputs"] = inputs
        self.engine.store.upsert_work_item(run_id, WorkItem.from_mapping(payload))
        self.engine.store.append_event(
            run_id,
            "work_item_status",
            {"id": item.id, "status": status, "diagnostics": diagnostic_records[:3]},
        )

    def _build_repair_packet(
        self,
        item: WorkItem,
        diagnostics: List[DiagnosticRecord],
        *,
        repair_ticket_id: str = "",
        repair_mode: str = "",
    ) -> Dict[str, Any]:
        editable_tests = self._work_item_targets_tests(item) or any(
            diagnostic.failure_kind == "invalid_tests"
            or diagnostic.recovery_action in {"test_repair", "test_regeneration", RECOVER_TEST}
            for diagnostic in diagnostics
        )
        test_artifacts: List[str] = []
        for diagnostic in diagnostics:
            for artifact in [
                *diagnostic.test_artifacts,
                *diagnostic.affected_artifacts,
                *diagnostic.external_artifacts,
            ]:
                normalized = self._normalize_repair_path(artifact)
                if normalized and self._is_test_artifact(normalized) and normalized not in test_artifacts:
                    test_artifacts.append(normalized)
        locked_artifacts = [] if editable_tests else test_artifacts
        return {
            "protocol_version": "2",
            "repair_ticket_id": repair_ticket_id,
            "repair_mode": repair_mode,
            "owner_scope": item.scope_id,
            "allowed_artifacts": list(item.target_artifacts),
            "locked_artifacts": locked_artifacts,
            "editable_tests": editable_tests,
            "diagnostic_fingerprints": [diagnostic.fingerprint() for diagnostic in diagnostics if diagnostic.fingerprint()],
            "diagnostics": [diagnostic.to_record() for diagnostic in diagnostics[:5]],
            "instruction": next((diagnostic.repair_instruction for diagnostic in diagnostics if diagnostic.repair_instruction), ""),
        }

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        normalized = path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    @classmethod
    def _work_item_targets_tests(cls, item: WorkItem) -> bool:
        return any(cls._is_test_artifact(path) for path in item.target_artifacts)

    def _repair_limit_for_item(self, item: WorkItem, guardrails: Dict[str, int], limit_key: str) -> int:
        if limit_key == "test_repair_limit" or self._work_item_targets_tests(item):
            item_limit = int(guardrails.get("item_repair_limit", self.engine.config.AUTO_ITEM_REPAIR_MAX))
            return int(guardrails.get("test_repair_limit", max(item_limit, self.engine.config.AUTO_TEST_REPAIR_MAX)))
        return int(guardrails.get(limit_key, self.engine.config.AUTO_ITEM_REPAIR_MAX))

    def _failure_kind_from_item(self, run_id: str, item_id: str) -> str:
        latest = self.engine.store.latest_step_for_item(run_id, item_id)
        text = latest.error if latest else ""
        if latest is not None:
            try:
                import json

                text = "\n".join(
                    part
                    for part in (
                        latest.error,
                        json.dumps(latest.output or {}, ensure_ascii=False),
                    )
                    if part
                )
            except TypeError:
                text = "\n".join(part for part in (latest.error, str(latest.output or "")) if part)
        lowered = text.lower()
        if (
            "runs.sqlite" in lowered
            or "sandbox" in lowered
            or "tool intent" in lowered
            or "infra_failure" in lowered
            or '"failure_kind": "infra"' in lowered
            or "llm backend" in lowered
            or "llm infrastructure failure" in lowered
            or "failed to create session" in lowered
            or "attempt to write a readonly database" in lowered
            or "operation not permitted" in lowered
        ):
            return FAILURE_INFRA
        if "dependency cycle" in lowered or "unknown scope" in lowered or "interface missing" in lowered:
            return FAILURE_CONTRACT_PLAN
        if "invalid blocker" in lowered or "required artifacts are already allowed" in lowered:
            return FAILURE_ITEM_QUALITY
        if (
            "requires approval" in lowered
            or "permission denied" in lowered
            or "requires approved source access" in lowered
            or "provided source material" in lowered
            or "source access unavailable" in lowered
            or "agent reported a blocker" in lowered
            or "out_of_scope_repair" in lowered
            or "out-of-scope repair" in lowered
            or "outside this workitem" in lowered
            or "outside this work item" in lowered
            or "outside allowed artifacts" in lowered
        ):
            return FAILURE_HUMAN_REQUIRED
        return FAILURE_ITEM_QUALITY

    def _counter_value(self, run_id: str, counter_key: str, item_id: str) -> int:
        run = self.engine.store.get_run(run_id)
        counters = dict((run.metadata if run else {}).get("autonomy_guardrails", {}))
        return int(dict(counters.get(counter_key, {})).get(item_id, 0))

    def _increment_counter(self, run_id: str, counter_key: str, item_id: str) -> None:
        run = self.engine.store.get_run(run_id)
        metadata = dict(run.metadata if run else {})
        counters = dict(metadata.get("autonomy_guardrails", {}))
        bucket = dict(counters.get(counter_key, {}))
        bucket[item_id] = int(bucket.get(item_id, 0)) + 1
        counters[counter_key] = bucket
        metadata["autonomy_guardrails"] = counters
        self.engine.store.update_run_status(run_id, run.status if run else "BLOCKED", metadata)

    def _increment_item_counter(self, run_id: str, counter_key: str, item_id: str) -> None:
        """Keep an aggregate WorkItem counter alongside scoped repair fingerprints."""

        run = self.engine.store.get_run(run_id)
        metadata = dict(run.metadata if run else {})
        counters = dict(metadata.get("autonomy_guardrails", {}))
        bucket = dict(counters.get(counter_key, {}))
        bucket[item_id] = int(bucket.get(item_id, 0)) + 1
        counters[counter_key] = bucket
        metadata["autonomy_guardrails"] = counters
        self.engine.store.update_run_status(run_id, run.status if run else "BLOCKED", metadata)


class RecoveryController:
    """Expose autonomous recovery without making Task or Contract own runtime state."""

    def __init__(self, engine: "RunEngine"):
        self.engine = engine

    def continue_run(self, task_or_run_id: str, max_steps: Optional[int] = None) -> "AutoRunResult":
        return self.engine.auto_steward.continue_run(
            self.engine.resolve_run_id(task_or_run_id),
            max_steps=max_steps,
        )
