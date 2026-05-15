"""RegistryTool — agent-facing producer/consumer API.

This is the only object workers should touch when interacting with the
contract registry. Every call:
  1. Resolves a logical path
  2. Enforces ACL via `RegistryACL`
  3. Stamps the call with a `MarginAnnotation`
  4. Forwards to `RegistryBackend` for durable I/O

Producer side (write): publish capsule, submit typed contract operations,
write reduced contract state, append validation evidence, emit events, append
progress / failure / decision, resolve escalation.

Consumer side (read, pull-based — C2): list capsules visible to the actor,
fetch a capsule at a chosen layer (L1 tag / L2 interface / L3 path), tail
events since timestamp, read contract state, and read team ledgers (for own
team only).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional

from ..contract.capsule import (
    CapsuleStatus,
    InterfaceCapsuleV2,
)
from ..contract.diff import FileChange, sha256_text
from ..contract.evidence import ValidationEvidence
from ..core.events import Event, EventKind
from ..contract.lifecycle import (
    TransitionResult,
    advance,
    record_smoke_failure,
    reset_smoke_failures,
)
from ..core.margin import AgentRole, MarginAnnotation
from ..contract.project import PlanSpec
from ..contract.kernel import ProjectContract, TeamContract
from ..contract.operation import ContractObligation, ContractOperation
from ..contract.work import TeamScheduleReport, TeamWorkItem
from ..contract.team import (
    Decision,
    TeamSubContract,
    WorkingPaper,
)
from ..memory.interaction import Interaction, InteractionLog
from ..memory.ledgers import (
    FailedHypothesis,
    ProgressEntry,
    TaskItem,
    TaskLedger,
    TaskStatus,
)
from ..memory.reviewer_memory import ReviewerMemory
from .acl import Actor, Op, RegistryACL, RegistryAccessError
from .backend import RegistryBackend, RegistryPath


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------


PLAN_PATH = RegistryPath("/plan.json")
PROJECT_CONTRACT_PATH = RegistryPath("/contract/project.json")
OPERATIONS_PATH = RegistryPath("/contract/operations.jsonl")
OBLIGATIONS_PATH = RegistryPath("/contract/obligations.jsonl")
SCHEDULE_PATH = RegistryPath("/contract/schedule.jsonl")
EVIDENCE_PATH = RegistryPath("/contract/evidence.jsonl")


def _team_contract_path(team_id: str) -> RegistryPath:
    return RegistryPath(f"/contract/teams/{team_id}.json")


def _team_contract_dir() -> RegistryPath:
    return RegistryPath("/contract/teams/")


def _capsule_path(team_id: str, capability: str) -> RegistryPath:
    return RegistryPath(f"/capsules/{team_id}/{capability}.json")


def _capsule_dir(team_id: str) -> RegistryPath:
    return RegistryPath(f"/capsules/{team_id}/")


def _ledger_dir(team_id: str) -> RegistryPath:
    return RegistryPath(f"/ledgers/{team_id}/")


def _working_paper_path(team_id: str) -> RegistryPath:
    return RegistryPath(f"/ledgers/{team_id}/working_paper.json")


def _task_ledger_path(team_id: str) -> RegistryPath:
    return RegistryPath(f"/ledgers/{team_id}/task_ledger.json")


def _progress_path(team_id: str) -> RegistryPath:
    return RegistryPath(f"/ledgers/{team_id}/progress_ledger.jsonl")


def _failure_path(team_id: str) -> RegistryPath:
    return RegistryPath(f"/ledgers/{team_id}/failure_ledger.jsonl")


def _reviewer_memory_path(team_id: str) -> RegistryPath:
    return RegistryPath(f"/ledgers/{team_id}/reviewer_memory.json")


def _interaction_path(team_id: str) -> RegistryPath:
    """Append-only natural-language interaction stream.

    Lives under `/ledgers/<team>/interactions.jsonl` so it inherits the
    existing path-scoped ACL: team-private, coordinator-readable.
    """
    return RegistryPath(f"/ledgers/{team_id}/interactions.jsonl")


def _escalation_path(escalation_id: str) -> RegistryPath:
    return RegistryPath(f"/escalations/{escalation_id}.json")


# ---------------------------------------------------------------------------
# RegistryTool
# ---------------------------------------------------------------------------


class RegistryTool:
    """Agent-facing facade.

    Parameters
    ----------
    backend:
        Underlying durable store.
    acl:
        Policy enforcer.
    actor:
        WHO is calling. Bind a fresh `RegistryTool` per agent so every call
        is automatically attributed.
    """

    def __init__(self, backend: RegistryBackend, acl: RegistryACL, actor: Actor):
        self.backend = backend
        self.acl = acl
        self.actor = actor

    # ------------------------------------------------------------------ utils

    def _enforce(self, op: Op, path: RegistryPath) -> None:
        self.acl.enforce(self.actor, op, path)

    def _margin(
        self,
        *,
        evidence: Optional[List[str]] = None,
        uncertainty: float = 0.0,
        parent_event_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> MarginAnnotation:
        return MarginAnnotation(
            author_agent=self.actor.agent_id,
            author_role=self.actor.role,
            team_id=self.actor.team_id,
            source_evidence=list(evidence or []),
            uncertainty=uncertainty,
            parent_event_id=parent_event_id,
            details=dict(details or {}),
        )

    # ------------------------------------------------------------------ plan

    def get_plan(self) -> Optional[PlanSpec]:
        self._enforce(Op.READ, PLAN_PATH)
        raw = self.backend.read_json(PLAN_PATH)
        return PlanSpec.from_mapping(raw) if raw else None

    def write_plan(self, plan: PlanSpec, *, freeze: bool = False) -> PlanSpec:
        self._enforce(Op.WRITE, PLAN_PATH)
        if freeze:
            plan.frozen = True
            plan.intent.frozen = True
        errors = plan.validate()
        if errors:
            raise ValueError(f"plan validation failed: {errors}")
        self.backend.write_json(PLAN_PATH, plan.to_record())
        if freeze:
            self.emit_event(
                EventKind.PLAN_FROZEN,
                team_id="*",
                payload={"plan_version": plan.plan_version, "team_ids": plan.team_ids()},
            )
        return plan

    # ---------------------------------------------------------- contract SSOT

    def get_project_contract(self) -> Optional[ProjectContract]:
        self._enforce(Op.READ, PROJECT_CONTRACT_PATH)
        raw = self.backend.read_json(PROJECT_CONTRACT_PATH)
        if raw:
            return ProjectContract.from_mapping(raw)
        plan = self.get_plan()
        return ProjectContract.from_plan(plan) if plan else None

    def write_project_contract(self, contract: ProjectContract) -> ProjectContract:
        self._enforce(Op.WRITE, PROJECT_CONTRACT_PATH)
        errors = contract.validate()
        if errors:
            raise ValueError(f"project contract validation failed: {errors}")
        self.backend.write_json(PROJECT_CONTRACT_PATH, contract.to_record())
        return contract

    def get_team_contract(self, team_id: str) -> Optional[TeamContract]:
        path = _team_contract_path(team_id)
        self._enforce(Op.READ, path)
        raw = self.backend.read_json(path)
        if raw:
            return TeamContract.from_mapping(raw)
        subcontract = self.get_team_subcontract(team_id)
        if subcontract:
            return TeamContract.from_subcontract(subcontract)
        return None

    def write_team_contract(self, contract: TeamContract) -> TeamContract:
        path = _team_contract_path(contract.team_id)
        self._enforce(Op.WRITE, path)
        self.backend.write_json(path, contract.to_record())
        return contract

    def list_team_contracts(self) -> List[TeamContract]:
        self._enforce(Op.LIST, _team_contract_dir())
        out: List[TeamContract] = []
        for entry in self.backend.list_dir(_team_contract_dir()):
            if not entry.endswith(".json"):
                continue
            contract = self.get_team_contract(entry[:-5])
            if contract is not None:
                out.append(contract)
        return out

    def append_contract_operation(self, operation: ContractOperation) -> ContractOperation:
        self._enforce(Op.APPEND, OPERATIONS_PATH)
        self.backend.append_jsonl(OPERATIONS_PATH, operation.to_record())
        return operation

    def read_contract_operations(self, *, limit: int = 0) -> List[ContractOperation]:
        self._enforce(Op.READ, OPERATIONS_PATH)
        return [
            ContractOperation.from_mapping(r)
            for r in self.backend.read_jsonl(OPERATIONS_PATH, limit=limit)
        ]

    def write_obligations(self, obligations: List[ContractObligation]) -> None:
        self._enforce(Op.WRITE, OBLIGATIONS_PATH)
        lines = [
            json.dumps(obl.to_record(), ensure_ascii=False, sort_keys=True)
            for obl in obligations
        ]
        self.backend.write_text(OBLIGATIONS_PATH, "\n".join(lines) + ("\n" if lines else ""))

    def read_obligations(self, *, limit: int = 0) -> List[ContractObligation]:
        self._enforce(Op.READ, OBLIGATIONS_PATH)
        return [
            ContractObligation.from_mapping(r)
            for r in self.backend.read_jsonl(OBLIGATIONS_PATH, limit=limit)
        ]

    def append_schedule(self, report: TeamScheduleReport) -> TeamScheduleReport:
        self._enforce(Op.APPEND, SCHEDULE_PATH)
        self.backend.append_jsonl(SCHEDULE_PATH, report.to_record())
        return report

    def read_schedules(self, *, limit: int = 0) -> List[TeamScheduleReport]:
        self._enforce(Op.READ, SCHEDULE_PATH)
        return [
            TeamScheduleReport.from_mapping(r)
            for r in self.backend.read_jsonl(SCHEDULE_PATH, limit=limit)
        ]

    def append_validation_evidence(self, evidence: ValidationEvidence) -> ValidationEvidence:
        self._enforce(Op.APPEND, EVIDENCE_PATH)
        self.backend.append_jsonl(EVIDENCE_PATH, evidence.to_record())
        return evidence

    def read_validation_evidence(self, *, limit: int = 0) -> List[ValidationEvidence]:
        self._enforce(Op.READ, EVIDENCE_PATH)
        return [
            ValidationEvidence.from_mapping(r)
            for r in self.backend.read_jsonl(EVIDENCE_PATH, limit=limit)
        ]

    def get_validation_evidence(self, evidence_id: str) -> Optional[ValidationEvidence]:
        for evidence in self.read_validation_evidence():
            if evidence.evidence_id == evidence_id:
                return evidence
        return None

    # --------------------------------------------------------------- capsule

    def list_capsules(
        self,
        *,
        team_id: Optional[str] = None,
        statuses: Optional[Iterable[CapsuleStatus]] = None,
    ) -> List[InterfaceCapsuleV2]:
        teams: List[str]
        if team_id:
            teams = [team_id]
        else:
            self._enforce(Op.LIST, RegistryPath("/capsules/"))
            teams = self.backend.list_dir(RegistryPath("/capsules/"))
        out: List[InterfaceCapsuleV2] = []
        for tid in teams:
            self._enforce(Op.LIST, _capsule_dir(tid))
            for entry in self.backend.list_dir(_capsule_dir(tid)):
                if not entry.endswith(".json"):
                    continue
                cap = self.get_capsule(tid, entry[:-5])
                if cap is None:
                    continue
                if statuses and cap.status not in set(statuses):
                    continue
                out.append(cap)
        return out

    def get_capsule(self, team_id: str, capability: str) -> Optional[InterfaceCapsuleV2]:
        path = _capsule_path(team_id, capability)
        self._enforce(Op.READ, path)
        raw = self.backend.read_json(path)
        return InterfaceCapsuleV2.from_mapping(raw) if raw else None

    def get_capsule_layer(
        self,
        team_id: str,
        capability: str,
        *,
        layer: str = "L1",
    ) -> Optional[Dict[str, Any]]:
        """Progressive disclosure read.

        layer:
            "L1" → tag (always allowed for any reader)
            "L2" → interface (consumers + owning team)
            "L3" → artifact paths (consumers + owning team)
        """
        cap = self.get_capsule(team_id, capability)
        if cap is None:
            return None
        if layer == "L1":
            payload = cap.tag.to_record() if cap.tag else None
        elif layer == "L2":
            self._assert_consumer_or_owner(cap)
            payload = cap.interface.to_record() if cap.interface else None
        elif layer == "L3":
            self._assert_consumer_or_owner(cap)
            payload = cap.artifacts.to_record()
        else:
            raise ValueError(f"unknown layer {layer!r}")
        if payload is None:
            return None
        return {
            "capsule_id": cap.capsule_id,
            "team_id": cap.team_id,
            "capability": cap.capability,
            "version": cap.version,
            "status": cap.status.value,
            "layer": layer,
            "payload": payload,
        }

    def _assert_consumer_or_owner(self, capsule: InterfaceCapsuleV2) -> None:
        if self.actor.role == AgentRole.COORDINATOR:
            return
        if self.actor.team_id == capsule.team_id:
            return
        if self.actor.team_id in capsule.consumers:
            return
        raise RegistryAccessError(
            f"team {self.actor.team_id!r} not declared consumer of "
            f"{capsule.team_id}/{capsule.capability}; cannot read layer L2/L3"
        )

    def publish_capsule(
        self,
        capsule: InterfaceCapsuleV2,
        *,
        target_status: CapsuleStatus = CapsuleStatus.DRAFT,
        reason: str = "",
        breaking_change: bool = False,
        evidence: Optional[List[str]] = None,
    ) -> TransitionResult:
        """Steward-side write: persist + advance lifecycle + emit event.

        If the capsule does not exist yet on disk, it is created in its
        current `status` first, then `advance()` moves it to `target_status`.
        """
        path = _capsule_path(capsule.team_id, capsule.capability)
        self._enforce(Op.WRITE, path)

        existing_raw = self.backend.read_json(path)
        if existing_raw is None:
            self.backend.write_json(path, capsule.to_record())
            self.emit_event(
                EventKind.CAPSULE_PROPOSED,
                team_id=capsule.team_id,
                payload={
                    "capsule_id": capsule.capsule_id,
                    "capability": capsule.capability,
                    "version": capsule.version,
                },
            )

        if capsule.status == target_status:
            self.backend.write_json(path, capsule.to_record())
            return TransitionResult(
                ok=True,
                from_status=capsule.status,
                to_status=capsule.status,
                errors=[],
            )

        margin = self._margin(evidence=evidence)
        result = advance(
            capsule,
            target_status,
            margin=margin,
            reason=reason,
            breaking_change=breaking_change,
        )
        if result.ok:
            self.backend.write_json(path, capsule.to_record())
            kind_map = {
                CapsuleStatus.DRAFT: EventKind.CAPSULE_DRAFTED,
                CapsuleStatus.LOCKED: EventKind.CAPSULE_LOCKED,
                CapsuleStatus.EVOLVED: EventKind.CAPSULE_EVOLVED,
                CapsuleStatus.BROKEN: EventKind.CAPSULE_BROKEN,
            }
            kind = kind_map.get(target_status)
            if kind:
                self.emit_event(
                    kind,
                    team_id=capsule.team_id,
                    payload={
                        "capsule_id": capsule.capsule_id,
                        "capability": capsule.capability,
                        "version": capsule.version,
                        "reason": reason,
                    },
                )
        return result

    def add_consumer(self, team_id: str, capability: str, consumer_team: str) -> bool:
        """Declare a consumer of a capsule (required before LOCK)."""
        path = _capsule_path(team_id, capability)
        self._enforce(Op.WRITE, path)
        capsule = self.get_capsule(team_id, capability)
        if capsule is None:
            return False
        if consumer_team in capsule.consumers:
            return True
        capsule.consumers.append(consumer_team)
        self.backend.write_json(path, capsule.to_record())
        return True

    def record_capsule_smoke_failure(
        self,
        team_id: str,
        capability: str,
        *,
        threshold: int = 3,
        evidence: Optional[List[str]] = None,
        reason: str = "",
    ) -> Optional[TransitionResult]:
        path = _capsule_path(team_id, capability)
        self._enforce(Op.WRITE, path)
        capsule = self.get_capsule(team_id, capability)
        if capsule is None:
            return None
        broken = record_smoke_failure(capsule, threshold=threshold)
        self.backend.write_json(path, capsule.to_record())
        if broken and capsule.status != CapsuleStatus.BROKEN:
            margin = self._margin(evidence=evidence)
            result = advance(
                capsule,
                CapsuleStatus.BROKEN,
                margin=margin,
                reason=reason or f"smoke failed {capsule.smoke_failures}× ≥ {threshold}",
            )
            if result.ok:
                self.backend.write_json(path, capsule.to_record())
                self.emit_event(
                    EventKind.CAPSULE_BROKEN,
                    team_id=capsule.team_id,
                    payload={
                        "capsule_id": capsule.capsule_id,
                        "capability": capsule.capability,
                        "smoke_failures": capsule.smoke_failures,
                    },
                )
            return result
        return None

    def reset_capsule_smoke(self, team_id: str, capability: str) -> bool:
        path = _capsule_path(team_id, capability)
        self._enforce(Op.WRITE, path)
        capsule = self.get_capsule(team_id, capability)
        if capsule is None:
            return False
        reset_smoke_failures(capsule)
        self.backend.write_json(path, capsule.to_record())
        return True

    # --------------------------------------------------------------- events

    def emit_event(
        self,
        kind: EventKind,
        *,
        team_id: str,
        payload: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[str]] = None,
        parent_event_id: Optional[str] = None,
        uncertainty: float = 0.0,
    ) -> Event:
        path = RegistryPath("/events.log")
        self._enforce(Op.APPEND, path)
        event = Event(
            kind=kind,
            team_id=team_id,
            payload=dict(payload or {}),
            margin=self._margin(
                evidence=evidence,
                uncertainty=uncertainty,
                parent_event_id=parent_event_id,
            ),
        )
        return self.backend.emit_event(event)

    def tail_events(
        self,
        *,
        since_ts: float = 0.0,
        kinds: Optional[Iterable[EventKind]] = None,
        team_id: str = "",
        limit: int = 0,
    ) -> List[Event]:
        self._enforce(Op.READ, RegistryPath("/events.log"))
        return self.backend.read_events(
            since_ts=since_ts, kinds=kinds, team_id=team_id, limit=limit
        )

    # ---------------------------------------------------------- subcontract

    def get_team_subcontract(self, team_id: str) -> TeamSubContract:
        wp = self.get_working_paper(team_id) or WorkingPaper(team_id=team_id)
        tl = self.get_task_ledger(team_id) or TaskLedger(team_id=team_id)
        rm = self.get_reviewer_memory(team_id) or ReviewerMemory(team_id=team_id)
        failures = self.list_failures(team_id)
        return TeamSubContract(
            team_id=team_id,
            working_paper=wp,
            task_ledger=tl,
            failure_ledger=failures,
            reviewer_memory=rm,
        )

    # --------- working paper ---------

    def get_working_paper(self, team_id: str) -> Optional[WorkingPaper]:
        path = _working_paper_path(team_id)
        self._enforce(Op.READ, path)
        raw = self.backend.read_json(path)
        return WorkingPaper.from_mapping(raw) if raw else None

    def write_working_paper(self, paper: WorkingPaper) -> None:
        path = _working_paper_path(paper.team_id)
        self._enforce(Op.WRITE, path)
        self.backend.write_json(path, paper.to_record())

    def append_decision(
        self,
        team_id: str,
        statement: str,
        rationale: str = "",
        *,
        evidence: Optional[List[str]] = None,
        uncertainty: float = 0.0,
    ) -> Decision:
        paper = self.get_working_paper(team_id) or WorkingPaper(team_id=team_id)
        margin = self._margin(evidence=evidence, uncertainty=uncertainty)
        decision = paper.add_decision(statement, rationale, margin)
        self.write_working_paper(paper)
        self.emit_event(
            EventKind.DECISION,
            team_id=team_id,
            payload={"decision_id": decision.decision_id, "statement": statement},
            evidence=evidence,
            uncertainty=uncertainty,
        )
        return decision

    # --------- task ledger ---------

    def get_task_ledger(self, team_id: str) -> Optional[TaskLedger]:
        path = _task_ledger_path(team_id)
        self._enforce(Op.READ, path)
        raw = self.backend.read_json(path)
        return TaskLedger.from_mapping(raw) if raw else None

    def write_task_ledger(self, ledger: TaskLedger) -> None:
        path = _task_ledger_path(ledger.team_id)
        self._enforce(Op.WRITE, path)
        self.backend.write_json(path, ledger.to_record())

    def upsert_task(self, team_id: str, task: TaskItem) -> TaskItem:
        if isinstance(task, TeamWorkItem):
            task = task.to_task_item()
        ledger = self.get_task_ledger(team_id) or TaskLedger(team_id=team_id)
        existing = ledger.by_id(task.task_id)
        if existing:
            for field_ in (
                "title",
                "goal",
                "output_format",
                "tool_whitelist",
                "boundaries",
                "status",
                "capsule_dependencies",
                "attempts",
            ):
                setattr(existing, field_, getattr(task, field_))
        else:
            ledger.add(task)
        self.write_task_ledger(ledger)
        return ledger.by_id(task.task_id) or task

    def set_task_status(self, team_id: str, task_id: str, status: TaskStatus) -> None:
        ledger = self.get_task_ledger(team_id)
        if ledger is None:
            return
        item = ledger.by_id(task_id)
        if item is None:
            return
        item.status = status
        self.write_task_ledger(ledger)

    # --------- progress ledger ---------

    def append_progress(
        self,
        team_id: str,
        *,
        task_id: str,
        kind: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[str]] = None,
        uncertainty: float = 0.0,
    ) -> ProgressEntry:
        path = _progress_path(team_id)
        self._enforce(Op.APPEND, path)
        entry = ProgressEntry(
            entry_id=f"prog:{int(time.time() * 1000)}",
            task_id=task_id,
            kind=kind,
            summary=summary,
            margin=self._margin(evidence=evidence, uncertainty=uncertainty),
            payload=dict(payload or {}),
        )
        self.backend.append_jsonl(path, entry.to_record())
        return entry

    def read_progress(self, team_id: str, *, limit: int = 0) -> List[ProgressEntry]:
        path = _progress_path(team_id)
        self._enforce(Op.READ, path)
        raws = self.backend.read_jsonl(path, limit=limit)
        return [ProgressEntry.from_mapping(r) for r in raws]

    # --------- failure ledger ---------

    def append_failure(
        self,
        team_id: str,
        failure: FailedHypothesis,
    ) -> FailedHypothesis:
        path = _failure_path(team_id)
        self._enforce(Op.APPEND, path)
        # ensure failure carries actor margin if not already
        if not failure.margin.author_agent:
            failure.margin = self._margin()
        self.backend.append_jsonl(path, failure.to_record())
        self.emit_event(
            EventKind.FAILURE_LOGGED,
            team_id=team_id,
            payload={
                "fingerprint": failure.fingerprint,
                "what_was_tried": failure.what_was_tried[:120],
            },
        )
        return failure

    def list_failures(self, team_id: str, *, limit: int = 0) -> List[FailedHypothesis]:
        path = _failure_path(team_id)
        self._enforce(Op.READ, path)
        raws = self.backend.read_jsonl(path, limit=limit)
        return [FailedHypothesis.from_mapping(r) for r in raws]

    # --------- reviewer memory ---------

    def get_reviewer_memory(self, team_id: str) -> Optional[ReviewerMemory]:
        path = _reviewer_memory_path(team_id)
        self._enforce(Op.READ, path)
        raw = self.backend.read_json(path)
        return ReviewerMemory.from_mapping(raw) if raw else None

    def write_reviewer_memory(self, memory: ReviewerMemory) -> None:
        path = _reviewer_memory_path(memory.team_id)
        self._enforce(Op.WRITE, path)
        self.backend.write_json(path, memory.to_record())

    # ------------------------------------------------------------ escalation

    def file_escalation(
        self,
        *,
        escalation_id: str,
        title: str,
        team_id: str,
        details: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        path = _escalation_path(escalation_id)
        self._enforce(Op.WRITE, path)
        record = {
            "escalation_id": escalation_id,
            "title": title,
            "team_id": team_id,
            "status": "open",
            "details": dict(details or {}),
            "margin": self._margin(evidence=evidence).to_record(),
            "created_at": time.time(),
        }
        self.backend.write_json(path, record)
        self.emit_event(
            EventKind.ESCALATED,
            team_id=team_id,
            payload={"escalation_id": escalation_id, "title": title},
            evidence=evidence,
        )
        return record

    def resolve_escalation(
        self,
        escalation_id: str,
        *,
        resolution: str,
        evidence: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        path = _escalation_path(escalation_id)
        self._enforce(Op.WRITE, path)
        record = self.backend.read_json(path)
        if not record:
            return None
        record["status"] = "resolved"
        record["resolution"] = resolution
        record["resolved_margin"] = self._margin(evidence=evidence).to_record()
        record["resolved_at"] = time.time()
        self.backend.write_json(path, record)
        self.emit_event(
            EventKind.ESCALATION_RESOLVED,
            team_id=record.get("team_id", ""),
            payload={"escalation_id": escalation_id, "resolution": resolution},
        )
        return record

    def list_open_escalations(self) -> List[Dict[str, Any]]:
        self._enforce(Op.LIST, RegistryPath("/escalations/"))
        out: List[Dict[str, Any]] = []
        for entry in self.backend.list_dir(RegistryPath("/escalations/")):
            if not entry.endswith(".json"):
                continue
            esc_id = entry[:-5]
            self._enforce(Op.READ, _escalation_path(esc_id))
            record = self.backend.read_json(_escalation_path(esc_id))
            if record and record.get("status") == "open":
                out.append(record)
        return out

    # --------------------------------------------------------- interactions

    def append_interaction(
        self,
        team_id: str,
        interaction: Interaction,
    ) -> Interaction:
        """Append a natural-language interaction to the team's stream."""
        path = _interaction_path(team_id)
        self._enforce(Op.APPEND, path)
        if not interaction.margin.author_agent:
            interaction.margin = self._margin()
        self.backend.append_jsonl(path, interaction.to_record())
        return interaction

    def read_interactions(
        self,
        team_id: str,
        *,
        limit: int = 0,
    ) -> InteractionLog:
        path = _interaction_path(team_id)
        self._enforce(Op.READ, path)
        raws = self.backend.read_jsonl(path, limit=limit)
        log = InteractionLog(team_id=team_id)
        for raw in raws:
            log.items.append(Interaction.from_mapping(raw))
        return log

    # --------------------------------------------------- artifact passthrough

    def write_workspace_text(self, team_id: str, rel_path: str, content: str) -> str:
        path = RegistryPath(f"/workspace/{team_id}/{rel_path}")
        self._enforce(Op.WRITE, path)
        self.backend.write_text(path, content)
        return path.normalised()

    def write_workspace_text_checked(
        self,
        team_id: str,
        rel_path: str,
        content: str,
        *,
        expected_sha256: str,
    ) -> FileChange:
        path = RegistryPath(f"/workspace/{team_id}/{rel_path}")
        self._enforce(Op.WRITE, path)
        written, observed_sha = self.backend.write_text_if_hash(
            path,
            content,
            expected_sha256=expected_sha256,
        )
        after_sha = sha256_text(content)
        if not written:
            return FileChange(
                path=rel_path,
                before_sha256=expected_sha256,
                after_sha256=after_sha,
                status="conflict",
                conflict=True,
                conflict_reason="workspace file changed since base read",
                expected_sha256=expected_sha256,
                observed_sha256=observed_sha,
            )
        status = "added" if not expected_sha256 else "modified"
        if expected_sha256 == after_sha:
            status = "unchanged"
        return FileChange(
            path=rel_path,
            before_sha256=expected_sha256,
            after_sha256=after_sha,
            status=status,
            expected_sha256=expected_sha256,
            observed_sha256=observed_sha,
        )

    def read_workspace_text(self, team_id: str, rel_path: str) -> Optional[str]:
        path = RegistryPath(f"/workspace/{team_id}/{rel_path}")
        self._enforce(Op.READ, path)
        return self.backend.read_text(path)

    def list_workspace(self, team_id: str, rel_path: str = "") -> List[str]:
        suffix = rel_path.lstrip("/")
        path = RegistryPath(f"/workspace/{team_id}/{suffix}")
        self._enforce(Op.LIST, path)
        return self.backend.list_dir(path)
