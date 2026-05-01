"""SQLite-backed run ledger for long-running agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

from ContractCoding.contract.spec import ContractSpec, load_contract_json
from ContractCoding.contract.work_item import WorkItem, normalize_work_item_status
from ContractCoding.runtime.fsm import WorkItemStateMachine


RUN_STATUSES = {"PENDING", "RUNNING", "PAUSED", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}
STEP_STATUSES = {"PENDING", "RUNNING", "COMPLETED", "ERROR", "SKIPPED"}
GATE_STATUSES = {"PENDING", "RUNNING", "PASSED", "FAILED", "BLOCKED"}
REPAIR_TICKET_LANES = {
    "local",
    "convergence",
    "impact_replan",
    "local_patch",
    "team_convergence",
    "interface_delta",
    "architecture_delta",
    "integration_convergence",
    "test_regeneration",
    "system_sync_repair",
    "phase_convergence",
    "contract_delta",
}
REPAIR_TICKET_STATUSES = {"OPEN", "RUNNING", "RESOLVED", "BLOCKED", "SUPERSEDED"}


def utc_now() -> str:
    return datetime.utcnow().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass
class RunRecord:
    id: str
    task: str
    status: str
    workspace_dir: str
    created_at: str
    updated_at: str
    metadata: Dict[str, Any]


@dataclass
class StepRecord:
    id: int
    run_id: str
    work_item_id: str
    agent_profile: str
    status: str
    attempt: int
    input: Dict[str, Any]
    output: Dict[str, Any]
    error: str
    created_at: str
    updated_at: str


@dataclass
class EventRecord:
    id: int
    run_id: str
    event_type: str
    payload: Dict[str, Any]
    created_at: str


@dataclass
class TeamRunRecord:
    id: str
    run_id: str
    scope_id: str
    status: str
    execution_plane: str
    work_item_ids: List[str]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


@dataclass
class GateRecord:
    run_id: str
    gate_id: str
    gate_type: str
    scope_id: str
    status: str
    evidence: List[str]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


@dataclass
class TaskRecord:
    id: str
    prompt: str
    workspace_dir: str
    backend: str
    active_run_id: str
    status_summary: Dict[str, Any]
    created_at: str
    updated_at: str


@dataclass
class RepairTicketRecord:
    id: str
    run_id: str
    ticket_key: str
    lane: str
    status: str
    source_gate: str
    source_item_id: str
    diagnostic_fingerprint: str
    owner_scope: str
    owner_artifacts: List[str]
    affected_scopes: List[str]
    conflict_keys: List[str]
    failure_summary: str
    expected_actual: str
    repair_instruction: str
    attempt_count: int
    evidence_refs: List[str]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


class RunStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self.work_item_fsm = WorkItemStateMachine()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._ensure_schema()

    @classmethod
    def for_workspace(cls, workspace_dir: str, db_path: str = "") -> "RunStore":
        if db_path:
            return cls(db_path)
        state_dir = os.path.join(os.path.abspath(workspace_dir), ".contractcoding")
        return cls(os.path.join(state_dir, "runs.sqlite"))

    def create_run(
        self,
        task: str,
        workspace_dir: str,
        work_items: Optional[Iterable[WorkItem]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        contract: Optional[ContractSpec] = None,
    ) -> str:
        run_id = uuid4().hex
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, task, status, workspace_dir, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, task, "PENDING", os.path.abspath(workspace_dir), now, now, _json_dumps(metadata or {})),
            )
        for item in work_items or []:
            self.upsert_work_item(run_id, item)
        if contract is not None:
            self.save_contract_version(run_id, contract)
            for item in contract.work_items:
                self.upsert_work_item(run_id, item)
            self.sync_gates(run_id, contract)
        self.append_event(run_id, "run_created", {"task": task})
        return run_id

    def create_task(
        self,
        *,
        prompt: str,
        workspace_dir: str,
        backend: str,
        status_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        task_id = uuid4().hex
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, prompt, workspace_dir, backend, active_run_id,
                    status_summary_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    prompt,
                    os.path.abspath(workspace_dir),
                    backend,
                    "",
                    _json_dumps(status_summary or {"status": "PENDING"}),
                    now,
                    now,
                ),
            )
        return task_id

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def find_task_by_run(self, run_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE active_run_id = ? LIMIT 1", (run_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def find_active_task_by_prompt(
        self,
        *,
        prompt: str,
        workspace_dir: str,
        backend: str = "",
        statuses: Sequence[str] = ("PENDING", "RUNNING", "PAUSED", "BLOCKED"),
    ) -> Optional[TaskRecord]:
        workspace = os.path.abspath(workspace_dir)
        query = """
            SELECT * FROM tasks
            WHERE prompt = ? AND workspace_dir = ? AND active_run_id != ''
        """
        params: List[Any] = [prompt, workspace]
        if backend:
            query += " AND backend = ?"
            params.append(backend)
        query += " ORDER BY updated_at DESC"
        allowed = {str(status) for status in statuses}
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        for row in rows:
            task = self._row_to_task(row)
            run = self.get_run(task.active_run_id)
            if run is not None and run.status in allowed:
                return task
        return None

    def list_tasks(self, limit: int = 20) -> List[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_task(
        self,
        task_id: str,
        *,
        active_run_id: Optional[str] = None,
        status_summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks SET active_run_id = ?, status_summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    active_run_id if active_run_id is not None else task.active_run_id,
                    _json_dumps(status_summary if status_summary is not None else task.status_summary),
                    now,
                    task_id,
                ),
            )

    def link_run_to_task(self, run_id: str, task_id: str) -> None:
        run = self.get_run(run_id)
        if run is None:
            return
        metadata = dict(run.metadata)
        metadata["task_id"] = task_id
        self.update_run_status(run_id, run.status, metadata)

    def save_contract_version(self, run_id: str, contract: ContractSpec) -> str:
        contract_hash = contract.content_hash()
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO contract_versions (run_id, contract_hash, contract_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, contract_hash) DO UPDATE SET
                    contract_json = excluded.contract_json
                """,
                (run_id, contract_hash, contract.to_json(), now),
            )
            run = self.get_run(run_id)
            metadata = dict(run.metadata if run else {})
            metadata["contract_hash"] = contract_hash
            conn.execute(
                "UPDATE runs SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (_json_dumps(metadata), now, run_id),
            )
        self.append_event(run_id, "contract_version_saved", {"contract_hash": contract_hash})
        return contract_hash

    def sync_gates(self, run_id: str, contract: ContractSpec) -> None:
        for phase in contract.phase_plan:
            self.ensure_gate(
                run_id=run_id,
                gate_id=f"phase:{phase.phase_id}",
                gate_type="phase",
                scope_id="phase",
                metadata={"spec": phase.to_record()},
            )
        for gate in contract.team_gates:
            self.ensure_gate(
                run_id=run_id,
                gate_id=f"team:{gate.scope_id}",
                gate_type="team",
                scope_id=gate.scope_id,
                metadata={"spec": gate.to_record()},
            )
        if contract.final_gate is not None:
            self.ensure_gate(
                run_id=run_id,
                gate_id="final",
                gate_type="final",
                scope_id="integration",
                metadata={"spec": contract.final_gate.to_record()},
            )

    def ensure_gate(
        self,
        *,
        run_id: str,
        gate_id: str,
        gate_type: str,
        scope_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GateRecord:
        now = utc_now()
        metadata = dict(metadata or {})
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? AND gate_id = ?",
                (run_id, gate_id),
            ).fetchone()
            if row:
                merged = _json_loads(row["metadata_json"], {})
                merged.update(metadata)
                conn.execute(
                    """
                    UPDATE gates SET gate_type = ?, scope_id = ?, metadata_json = ?, updated_at = ?
                    WHERE run_id = ? AND gate_id = ?
                    """,
                    (gate_type, scope_id, _json_dumps(merged), now, run_id, gate_id),
                )
                updated = conn.execute(
                    "SELECT * FROM gates WHERE run_id = ? AND gate_id = ?",
                    (run_id, gate_id),
                ).fetchone()
                return self._row_to_gate(updated)
            conn.execute(
                """
                INSERT INTO gates (
                    run_id, gate_id, gate_type, scope_id, status, evidence_json,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, gate_id, gate_type, scope_id, "PENDING", "[]", _json_dumps(metadata), now, now),
            )
            created = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? AND gate_id = ?",
                (run_id, gate_id),
            ).fetchone()
        self.append_event(run_id, "gate_planned", {"gate_id": gate_id, "gate_type": gate_type, "scope_id": scope_id})
        return self._row_to_gate(created)

    def get_gate(self, run_id: str, gate_id: str) -> Optional[GateRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? AND gate_id = ?",
                (run_id, gate_id),
            ).fetchone()
        return self._row_to_gate(row) if row else None

    def list_gates(self, run_id: str) -> List[GateRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? ORDER BY gate_type, scope_id, gate_id",
                (run_id,),
            ).fetchall()
        return [self._row_to_gate(row) for row in rows]

    def update_gate_status(
        self,
        run_id: str,
        gate_id: str,
        status: str,
        evidence: Optional[Iterable[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = status if status in GATE_STATUSES else "FAILED"
        gate = self.get_gate(run_id, gate_id)
        if gate is None:
            return
        now = utc_now()
        if normalized in {"RUNNING", "PASSED"}:
            gate_evidence = [str(value) for value in evidence or []]
        else:
            gate_evidence = list(gate.evidence)
            if evidence:
                gate_evidence.extend(str(value) for value in evidence)
        gate_metadata = dict(gate.metadata)
        if metadata:
            gate_metadata.update(metadata)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE gates SET status = ?, evidence_json = ?, metadata_json = ?, updated_at = ?
                WHERE run_id = ? AND gate_id = ?
                """,
                (normalized, _json_dumps(gate_evidence), _json_dumps(gate_metadata), now, run_id, gate_id),
            )
        self.append_event(
            run_id,
            "gate_status",
            {"gate_id": gate_id, "scope_id": gate.scope_id, "gate_type": gate.gate_type, "status": normalized},
        )

    def ensure_repair_ticket(
        self,
        *,
        run_id: str,
        lane: str,
        source_gate: str = "",
        source_item_id: str = "",
        diagnostic_fingerprint: str = "",
        owner_scope: str = "",
        owner_artifacts: Optional[Iterable[str]] = None,
        affected_scopes: Optional[Iterable[str]] = None,
        conflict_keys: Optional[Iterable[str]] = None,
        failure_summary: str = "",
        expected_actual: str = "",
        repair_instruction: str = "",
        evidence_refs: Optional[Iterable[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RepairTicketRecord:
        normalized_lane = lane if lane in REPAIR_TICKET_LANES else "local"
        owners = self._dedupe_strings(owner_artifacts or [])
        affected = self._dedupe_strings(affected_scopes or [])
        conflicts = self._dedupe_strings(conflict_keys or [])
        evidence = self._dedupe_strings(evidence_refs or [])
        ticket_key = self._repair_ticket_key(
            normalized_lane,
            source_gate=source_gate,
            source_item_id=source_item_id,
            diagnostic_fingerprint=diagnostic_fingerprint,
            owner_scope=owner_scope,
            owner_artifacts=owners,
        )
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repair_tickets WHERE run_id = ? AND ticket_key = ?",
                (run_id, ticket_key),
            ).fetchone()
            if row:
                existing = self._row_to_repair_ticket(row)
                merged_metadata = dict(existing.metadata)
                merged_metadata.update(metadata or {})
                merged_evidence = self._dedupe_strings([*existing.evidence_refs, *evidence])
                status = "OPEN" if existing.status in {"RESOLVED", "SUPERSEDED"} else existing.status
                conn.execute(
                    """
                    UPDATE repair_tickets SET lane = ?, status = ?, source_gate = ?,
                        source_item_id = ?, diagnostic_fingerprint = ?, owner_scope = ?,
                        owner_artifacts_json = ?, affected_scopes_json = ?,
                        conflict_keys_json = ?, failure_summary = ?, expected_actual = ?,
                        repair_instruction = ?, evidence_refs_json = ?, metadata_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_lane,
                        status,
                        source_gate,
                        source_item_id,
                        diagnostic_fingerprint,
                        owner_scope,
                        _json_dumps(owners),
                        _json_dumps(affected),
                        _json_dumps(conflicts),
                        failure_summary or existing.failure_summary,
                        expected_actual or existing.expected_actual,
                        repair_instruction or existing.repair_instruction,
                        _json_dumps(merged_evidence),
                        _json_dumps(merged_metadata),
                        now,
                        existing.id,
                    ),
                )
                updated = conn.execute("SELECT * FROM repair_tickets WHERE id = ?", (existing.id,)).fetchone()
                ticket = self._row_to_repair_ticket(updated)
            else:
                ticket_id = uuid4().hex
                conn.execute(
                    """
                    INSERT INTO repair_tickets (
                        id, run_id, ticket_key, lane, status, source_gate, source_item_id,
                        diagnostic_fingerprint, owner_scope, owner_artifacts_json,
                        affected_scopes_json, conflict_keys_json, failure_summary,
                        expected_actual, repair_instruction, attempt_count,
                        evidence_refs_json, metadata_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        run_id,
                        ticket_key,
                        normalized_lane,
                        "OPEN",
                        source_gate,
                        source_item_id,
                        diagnostic_fingerprint,
                        owner_scope,
                        _json_dumps(owners),
                        _json_dumps(affected),
                        _json_dumps(conflicts),
                        failure_summary,
                        expected_actual,
                        repair_instruction,
                        0,
                        _json_dumps(evidence),
                        _json_dumps(metadata or {}),
                        now,
                        now,
                    ),
                )
                created = conn.execute("SELECT * FROM repair_tickets WHERE id = ?", (ticket_id,)).fetchone()
                ticket = self._row_to_repair_ticket(created)
        self.append_event(
            run_id,
            "repair_ticket_opened",
            {
                "ticket_id": ticket.id,
                "lane": ticket.lane,
                "status": ticket.status,
                "owner_scope": ticket.owner_scope,
                "owner_artifacts": ticket.owner_artifacts,
                "source_gate": ticket.source_gate,
                "source_item_id": ticket.source_item_id,
                "diagnostic_fingerprint": ticket.diagnostic_fingerprint,
                "summary": ticket.failure_summary,
            },
        )
        return ticket

    def get_repair_ticket(self, ticket_id: str) -> Optional[RepairTicketRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM repair_tickets WHERE id = ?", (ticket_id,)).fetchone()
        return self._row_to_repair_ticket(row) if row else None

    def list_repair_tickets(
        self,
        run_id: str,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 200,
    ) -> List[RepairTicketRecord]:
        params: List[Any] = [run_id]
        query = "SELECT * FROM repair_tickets WHERE run_id = ?"
        wanted = [status for status in (statuses or []) if status in REPAIR_TICKET_STATUSES]
        if wanted:
            query += f" AND status IN ({','.join('?' for _ in wanted)})"
            params.extend(wanted)
        query += " ORDER BY created_at, id LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_repair_ticket(row) for row in rows]

    def ready_repair_tickets(self, run_id: str, limit: int = 200) -> List[RepairTicketRecord]:
        ready: List[RepairTicketRecord] = []
        occupied: set[str] = set()
        for ticket in self.list_repair_tickets(run_id, statuses={"OPEN"}, limit=limit):
            keys = set(ticket.conflict_keys or ticket.owner_artifacts or [ticket.owner_scope or ticket.id])
            if keys & occupied:
                continue
            ready.append(ticket)
            occupied.update(keys)
        return ready

    def update_repair_ticket_status(
        self,
        ticket_id: str,
        status: str,
        *,
        evidence_refs: Optional[Iterable[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = status if status in REPAIR_TICKET_STATUSES else "BLOCKED"
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM repair_tickets WHERE id = ?", (ticket_id,)).fetchone()
            if not row:
                return
            ticket = self._row_to_repair_ticket(row)
            evidence = self._dedupe_strings([*ticket.evidence_refs, *(evidence_refs or [])])
            merged = dict(ticket.metadata)
            if metadata:
                merged.update(metadata)
            conn.execute(
                """
                UPDATE repair_tickets SET status = ?, evidence_refs_json = ?,
                    metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized, _json_dumps(evidence), _json_dumps(merged), now, ticket_id),
            )
        self.append_event(
            ticket.run_id,
            "repair_ticket_status",
            {"ticket_id": ticket_id, "status": normalized, "lane": ticket.lane, "owner_scope": ticket.owner_scope},
        )

    def increment_repair_ticket_attempt(
        self,
        ticket_id: str,
        *,
        status: str = "RUNNING",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RepairTicketRecord:
        normalized = status if status in REPAIR_TICKET_STATUSES else "RUNNING"
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM repair_tickets WHERE id = ?", (ticket_id,)).fetchone()
            if not row:
                raise ValueError(f"Unknown repair ticket id: {ticket_id}")
            ticket = self._row_to_repair_ticket(row)
            merged = dict(ticket.metadata)
            if metadata:
                merged.update(metadata)
            conn.execute(
                """
                UPDATE repair_tickets SET status = ?, attempt_count = attempt_count + 1,
                    metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized, _json_dumps(merged), now, ticket_id),
            )
            updated = conn.execute("SELECT * FROM repair_tickets WHERE id = ?", (ticket_id,)).fetchone()
        ticket = self._row_to_repair_ticket(updated)
        self.append_event(
            ticket.run_id,
            "repair_ticket_attempt",
            {
                "ticket_id": ticket.id,
                "lane": ticket.lane,
                "attempt_count": ticket.attempt_count,
                "owner_scope": ticket.owner_scope,
                "owner_artifacts": ticket.owner_artifacts,
            },
        )
        return ticket

    @staticmethod
    def _repair_ticket_key(
        lane: str,
        *,
        source_gate: str,
        source_item_id: str,
        diagnostic_fingerprint: str,
        owner_scope: str,
        owner_artifacts: List[str],
    ) -> str:
        owner = ",".join(owner_artifacts[:4]) if owner_artifacts else owner_scope
        return "|".join(
            [
                lane,
                source_gate or "",
                source_item_id or "",
                diagnostic_fingerprint or "no-diagnostic",
                owner or "unowned",
            ]
        )

    @staticmethod
    def _dedupe_strings(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    def get_contract(self, run_id: str) -> Optional[ContractSpec]:
        run = self.get_run(run_id)
        contract_hash = str((run.metadata if run else {}).get("contract_hash", ""))
        query = "SELECT contract_json FROM contract_versions WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if contract_hash:
            query += " AND contract_hash = ?"
            params = (run_id, contract_hash)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        if not row:
            return None
        contract = load_contract_json(row["contract_json"])
        from ContractCoding.contract.compiler import ContractCompiler

        return ContractCompiler().compile(contract.goals[0] if contract.goals else "", contract)

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, limit: int = 20) -> List[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_run_status(self, run_id: str, status: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        normalized = status if status in RUN_STATUSES else "FAILED"
        now = utc_now()
        current = self.get_run(run_id)
        merged_metadata = dict(current.metadata if current else {})
        if metadata:
            merged_metadata.update(metadata)
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ?, metadata_json = ? WHERE id = ?",
                (normalized, now, _json_dumps(merged_metadata), run_id),
            )
        self.append_event(run_id, "run_status", {"status": normalized})

    def upsert_work_item(self, run_id: str, item: WorkItem) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO work_items (
                    run_id, id, kind, title, owner_profile, module, depends_on_json,
                    status, inputs_json, target_artifacts_json, acceptance_criteria_json,
                    evidence_json, risk_level, scope_id, serial_group, conflict_keys_json,
                    execution_mode, team_policy_json, provided_interfaces_json,
                    required_interfaces_json, dependency_policy, context_policy_json,
                    verification_policy_json, recovery_policy_json, team_role_hint,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, id) DO UPDATE SET
                    kind = excluded.kind,
                    title = excluded.title,
                    owner_profile = excluded.owner_profile,
                    module = excluded.module,
                    depends_on_json = excluded.depends_on_json,
                    status = excluded.status,
                    inputs_json = excluded.inputs_json,
                    target_artifacts_json = excluded.target_artifacts_json,
                    acceptance_criteria_json = excluded.acceptance_criteria_json,
                    evidence_json = excluded.evidence_json,
                    risk_level = excluded.risk_level,
                    scope_id = excluded.scope_id,
                    serial_group = excluded.serial_group,
                    conflict_keys_json = excluded.conflict_keys_json,
                    execution_mode = excluded.execution_mode,
                    team_policy_json = excluded.team_policy_json,
                    provided_interfaces_json = excluded.provided_interfaces_json,
                    required_interfaces_json = excluded.required_interfaces_json,
                    dependency_policy = excluded.dependency_policy,
                    context_policy_json = excluded.context_policy_json,
                    verification_policy_json = excluded.verification_policy_json,
                    recovery_policy_json = excluded.recovery_policy_json,
                    team_role_hint = excluded.team_role_hint,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    item.id,
                    item.kind,
                    item.title,
                    item.owner_profile,
                    item.module,
                    _json_dumps(item.depends_on),
                    item.status,
                    _json_dumps(item.inputs),
                    _json_dumps(item.target_artifacts),
                    _json_dumps(item.acceptance_criteria),
                    _json_dumps(item.evidence),
                    item.risk_level,
                    item.scope_id,
                    item.serial_group,
                    _json_dumps(item.conflict_keys),
                    item.execution_mode,
                    _json_dumps(item.team_policy),
                    _json_dumps(item.provided_interfaces),
                    _json_dumps(item.required_interfaces),
                    item.dependency_policy,
                    _json_dumps(item.context_policy),
                    _json_dumps(item.verification_policy),
                    _json_dumps(item.recovery_policy),
                    item.team_role_hint,
                    now,
                    now,
                ),
            )

    def sync_work_items(self, run_id: str, items: Iterable[WorkItem]) -> None:
        for item in items:
            existing = self.get_work_item(run_id, item.id)
            if existing and existing.status == "RUNNING":
                continue
            self.upsert_work_item(run_id, item)

    def get_work_item(self, run_id: str, item_id: str) -> Optional[WorkItem]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_items WHERE run_id = ? AND id = ?",
                (run_id, item_id),
            ).fetchone()
        return self._row_to_work_item(row) if row else None

    def list_work_items(self, run_id: str) -> List[WorkItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM work_items WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [self._row_to_work_item(row) for row in rows]

    def update_work_item_status(
        self,
        run_id: str,
        item_id: str,
        status: str,
        evidence: Optional[Iterable[str]] = None,
    ) -> None:
        item = self.get_work_item(run_id, item_id)
        if not item:
            return
        target_status = normalize_work_item_status(status)
        decision = self.work_item_fsm.can_transition(item.status, target_status)
        if not decision.allowed:
            raise ValueError(decision.reason)
        updated = item.with_status(target_status, evidence=evidence)
        self.upsert_work_item(run_id, updated)
        self.append_event(run_id, "work_item_status", {"id": item_id, "status": updated.status})

    def ready_work_items(self, run_id: str) -> List[WorkItem]:
        items = self.list_work_items(run_id)
        completed = {item.id for item in items if item.status in {"DONE", "VERIFIED"}}
        return [item for item in items if item.is_ready(completed)]

    def create_step(
        self,
        run_id: str,
        work_item_id: str,
        agent_profile: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = utc_now()
        attempt = self._next_attempt(run_id, work_item_id, agent_profile)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO steps (
                    run_id, work_item_id, agent_profile, status, attempt,
                    input_json, output_json, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    work_item_id,
                    agent_profile,
                    "RUNNING",
                    attempt,
                    _json_dumps(input_payload or {}),
                    _json_dumps({}),
                    "",
                    now,
                    now,
                ),
            )
            step_id = int(cursor.lastrowid)
        self.append_event(run_id, "step_started", {"step_id": step_id, "work_item_id": work_item_id})
        return step_id

    def finish_step(
        self,
        step_id: int,
        status: str,
        output_payload: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> None:
        normalized = status if status in STEP_STATUSES else "ERROR"
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE steps SET status = ?, output_json = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized, _json_dumps(output_payload or {}), error, now, step_id),
            )
            row = conn.execute("SELECT run_id, work_item_id FROM steps WHERE id = ?", (step_id,)).fetchone()
        if row:
            payload = {"step_id": step_id, "status": normalized, "work_item_id": row["work_item_id"]}
            timing = dict((output_payload or {}).get("timing", {})) if isinstance(output_payload, dict) else {}
            if timing:
                payload["timing"] = timing
            if error:
                payload["failure_category_hint"] = error[:300]
            self.append_event(row["run_id"], "step_finished", payload)

    def has_step(self, run_id: str, work_item_id: str, agent_profile: str, statuses: set[str]) -> bool:
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT 1 FROM steps
                WHERE run_id = ? AND work_item_id = ? AND agent_profile = ?
                  AND status IN ({placeholders})
                LIMIT 1
                """,
                (run_id, work_item_id, agent_profile, *sorted(statuses)),
            ).fetchone()
        return bool(row)

    def latest_steps(self, run_id: str, limit: int = 20) -> List[StepRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def count_steps(self, run_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS step_count FROM steps WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return int(row["step_count"] or 0)

    def latest_step_for_item(self, run_id: str, work_item_id: str) -> Optional[StepRecord]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM steps
                WHERE run_id = ? AND work_item_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, work_item_id),
            ).fetchone()
        return self._row_to_step(row) if row else None

    def count_error_steps_for_item(self, run_id: str, work_item_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS error_count FROM steps
                WHERE run_id = ? AND work_item_id = ? AND status = 'ERROR'
                """,
                (run_id, work_item_id),
            ).fetchone()
        return int(row["error_count"] or 0)

    def append_event(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (run_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, event_type, _json_dumps(payload), now),
            )

    def list_events(self, run_id: str, limit: int = 50) -> List[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def active_leased_items(self, run_id: str) -> set[str]:
        now = utc_now()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT work_item_id FROM work_item_leases
                WHERE run_id = ? AND expires_at > ?
                """,
                (run_id, now),
            ).fetchall()
        return {str(row["work_item_id"]) for row in rows}

    def acquire_leases(
        self,
        run_id: str,
        team_id: str,
        work_item_ids: Iterable[str],
        lease_seconds: int = 3600,
    ) -> bool:
        ids = [str(value) for value in work_item_ids]
        if not ids:
            return True
        now = utc_now()
        expires_at = datetime.utcnow().timestamp() + lease_seconds
        expires_iso = datetime.utcfromtimestamp(expires_at).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                f"""
                SELECT work_item_id FROM work_item_leases
                WHERE run_id = ? AND work_item_id IN ({','.join('?' for _ in ids)}) AND expires_at > ?
                """,
                (run_id, *ids, now),
            ).fetchall()
            if existing:
                return False
            for item_id in ids:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO work_item_leases (
                        run_id, work_item_id, team_id, owner, expires_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, item_id, team_id, team_id, expires_iso, now),
                )
        self.append_event(run_id, "leases_acquired", {"team_id": team_id, "work_item_ids": ids})
        return True

    def release_leases(self, run_id: str, team_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM work_item_leases WHERE run_id = ? AND team_id = ?",
                (run_id, team_id),
            )
        self.append_event(run_id, "leases_released", {"team_id": team_id})

    def create_team_run(
        self,
        run_id: str,
        scope_id: str,
        execution_plane: str,
        work_item_ids: Iterable[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        team_id = uuid4().hex
        now = utc_now()
        ids = [str(value) for value in work_item_ids]
        team_metadata = dict(metadata or {})
        team_metadata.setdefault("record_type", "wave")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO team_runs (
                    id, run_id, scope_id, status, execution_plane, work_item_ids_json,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    run_id,
                    scope_id,
                    "RUNNING",
                    execution_plane,
                    _json_dumps(ids),
                    _json_dumps(team_metadata),
                    now,
                    now,
                ),
            )
        self.append_event(run_id, "team_run_started", {"team_id": team_id, "scope_id": scope_id, "items": ids})
        return team_id

    def ensure_scope_team_run(self, run_id: str, spec_record: Dict[str, Any]) -> TeamRunRecord:
        logical_team_id = str(spec_record.get("team_id") or f"team:{spec_record.get('scope_id', 'root')}")
        team_id = f"{run_id}:{logical_team_id}"
        scope_id = str(spec_record.get("scope_id") or "root")
        execution_plane = str(spec_record.get("workspace_plane") or "workspace")
        ids = [str(value) for value in spec_record.get("owned_items", [])]
        metadata = {
            "record_type": "scope_team",
            "logical_team_id": logical_team_id,
            "team_kind": str(spec_record.get("team_kind") or "coding"),
            "roles": list(spec_record.get("roles", []) or []),
            "owned_artifacts": list(spec_record.get("owned_artifacts", []) or []),
            "conflict_keys": list(spec_record.get("conflict_keys", []) or []),
            "promotion_policy": dict(spec_record.get("promotion_policy", {}) or {}),
            "team_memory": dict(spec_record.get("team_memory", {}) or {}),
            "team_spec": dict(spec_record),
        }
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM team_runs WHERE id = ?", (team_id,)).fetchone()
            if row:
                merged = _json_loads(row["metadata_json"], {})
                preserved = {
                    key: value
                    for key, value in merged.items()
                    if key
                    in {
                        "plane",
                        "promoted_files",
                        "promotion_error",
                        "active_items",
                        "last_completed_items",
                        "last_failed_items",
                        "stale_dependency",
                        "stale_reason",
                    }
                }
                metadata.update(preserved)
                conn.execute(
                    """
                    UPDATE team_runs SET execution_plane = ?, work_item_ids_json = ?,
                        metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (execution_plane, _json_dumps(ids), _json_dumps(metadata), now, team_id),
                )
                updated = conn.execute("SELECT * FROM team_runs WHERE id = ?", (team_id,)).fetchone()
                return self._row_to_team_run(updated)

            status = str(spec_record.get("status") or "PLANNED")
            conn.execute(
                """
                INSERT INTO team_runs (
                    id, run_id, scope_id, status, execution_plane, work_item_ids_json,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    run_id,
                    scope_id,
                    status,
                    execution_plane,
                    _json_dumps(ids),
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )
            created = conn.execute("SELECT * FROM team_runs WHERE id = ?", (team_id,)).fetchone()
        self.append_event(
            run_id,
            "team_planned",
            {
                "team_id": team_id,
                "scope_id": scope_id,
                "team_kind": metadata["team_kind"],
                "execution_plane": execution_plane,
                "items": ids,
            },
        )
        return self._row_to_team_run(created)

    def get_team_run(self, team_id: str) -> Optional[TeamRunRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM team_runs WHERE id = ?", (team_id,)).fetchone()
        return self._row_to_team_run(row) if row else None

    def get_scope_team_run(self, run_id: str, scope_id: str) -> Optional[TeamRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM team_runs
                WHERE run_id = ? AND scope_id = ?
                ORDER BY created_at DESC
                """,
                (run_id, scope_id),
            ).fetchall()
        for row in rows:
            metadata = _json_loads(row["metadata_json"], {})
            if metadata.get("record_type") == "scope_team":
                return self._row_to_team_run(row)
        return None

    def list_scope_team_runs(self, run_id: str, limit: int = 200) -> List[TeamRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM team_runs
                WHERE run_id = ?
                ORDER BY created_at, scope_id
                """,
                (run_id,),
            ).fetchall()
        records = [
            self._row_to_team_run(row)
            for row in rows
            if _json_loads(row["metadata_json"], {}).get("record_type") == "scope_team"
        ]
        return records[:limit]

    def list_wave_team_runs(self, run_id: str, limit: int = 20) -> List[TeamRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM team_runs
                WHERE run_id = ?
                ORDER BY created_at DESC
                """,
                (run_id,),
            ).fetchall()
        records = [
            self._row_to_team_run(row)
            for row in rows
            if _json_loads(row["metadata_json"], {}).get("record_type", "wave") != "scope_team"
        ]
        return records[:limit]

    def update_team_run_status(
        self,
        team_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT run_id, scope_id, metadata_json FROM team_runs WHERE id = ?", (team_id,)).fetchone()
            if not row:
                return
            merged = _json_loads(row["metadata_json"], {})
            if metadata:
                merged.update(metadata)
            conn.execute(
                """
                UPDATE team_runs SET status = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, _json_dumps(merged), now, team_id),
            )
        self.append_event(
            row["run_id"],
            "team_status",
            {"team_id": team_id, "scope_id": row["scope_id"], "status": status},
        )

    def finish_team_run(
        self,
        team_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT run_id, metadata_json FROM team_runs WHERE id = ?", (team_id,)).fetchone()
            merged = _json_loads(row["metadata_json"], {}) if row else {}
            if metadata:
                merged.update(metadata)
            conn.execute(
                """
                UPDATE team_runs SET status = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, _json_dumps(merged), now, team_id),
            )
        if row:
            self.append_event(row["run_id"], "team_run_finished", {"team_id": team_id, "status": status})

    def list_team_runs(self, run_id: str, limit: int = 20) -> List[TeamRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM team_runs
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [self._row_to_team_run(row) for row in rows]

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    workspace_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    workspace_dir TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    active_run_id TEXT NOT NULL,
                    status_summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_items (
                    run_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    owner_profile TEXT NOT NULL,
                    module TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    target_artifacts_json TEXT NOT NULL,
                    acceptance_criteria_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT 'root',
                    serial_group TEXT NOT NULL DEFAULT '',
                    conflict_keys_json TEXT NOT NULL DEFAULT '[]',
                    execution_mode TEXT NOT NULL DEFAULT 'auto',
                    team_policy_json TEXT NOT NULL DEFAULT '{}',
                    provided_interfaces_json TEXT NOT NULL DEFAULT '[]',
                    required_interfaces_json TEXT NOT NULL DEFAULT '[]',
                    dependency_policy TEXT NOT NULL DEFAULT 'done',
                    context_policy_json TEXT NOT NULL DEFAULT '{}',
                    verification_policy_json TEXT NOT NULL DEFAULT '{}',
                    recovery_policy_json TEXT NOT NULL DEFAULT '{}',
                    team_role_hint TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, id)
                );

                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    agent_profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS contract_versions (
                    run_id TEXT NOT NULL,
                    contract_hash TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, contract_hash)
                );

                CREATE TABLE IF NOT EXISTS work_item_leases (
                    run_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, work_item_id)
                );

                CREATE TABLE IF NOT EXISTS team_runs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    execution_plane TEXT NOT NULL,
                    work_item_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gates (
                    run_id TEXT NOT NULL,
                    gate_id TEXT NOT NULL,
                    gate_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, gate_id)
                );

                CREATE TABLE IF NOT EXISTS repair_tickets (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    ticket_key TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_gate TEXT NOT NULL,
                    source_item_id TEXT NOT NULL,
                    diagnostic_fingerprint TEXT NOT NULL,
                    owner_scope TEXT NOT NULL,
                    owner_artifacts_json TEXT NOT NULL,
                    affected_scopes_json TEXT NOT NULL,
                    conflict_keys_json TEXT NOT NULL,
                    failure_summary TEXT NOT NULL,
                    expected_actual TEXT NOT NULL,
                    repair_instruction TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, ticket_key)
                );
                """
            )
            self._ensure_work_item_columns(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _next_attempt(self, run_id: str, work_item_id: str, agent_profile: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(attempt) AS max_attempt FROM steps
                WHERE run_id = ? AND work_item_id = ? AND agent_profile = ?
                """,
                (run_id, work_item_id, agent_profile),
            ).fetchone()
        return int(row["max_attempt"] or 0) + 1

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            task=row["task"],
            status=row["status"],
            workspace_dir=row["workspace_dir"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_json_loads(row["metadata_json"], {}),
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            prompt=row["prompt"],
            workspace_dir=row["workspace_dir"],
            backend=row["backend"],
            active_run_id=row["active_run_id"],
            status_summary=_json_loads(row["status_summary_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_work_item(row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            owner_profile=row["owner_profile"],
            module=row["module"],
            depends_on=_json_loads(row["depends_on_json"], []),
            status=row["status"],
            inputs=_json_loads(row["inputs_json"], {}),
            target_artifacts=_json_loads(row["target_artifacts_json"], []),
            acceptance_criteria=_json_loads(row["acceptance_criteria_json"], []),
            evidence=_json_loads(row["evidence_json"], []),
            risk_level=row["risk_level"],
            scope_id=row["scope_id"] if "scope_id" in row.keys() else "root",
            serial_group=row["serial_group"] if "serial_group" in row.keys() else "",
            conflict_keys=_json_loads(row["conflict_keys_json"], []) if "conflict_keys_json" in row.keys() else [],
            execution_mode=row["execution_mode"] if "execution_mode" in row.keys() else "auto",
            team_policy=_json_loads(row["team_policy_json"], {}) if "team_policy_json" in row.keys() else {},
            provided_interfaces=(
                _json_loads(row["provided_interfaces_json"], [])
                if "provided_interfaces_json" in row.keys()
                else []
            ),
            required_interfaces=(
                _json_loads(row["required_interfaces_json"], [])
                if "required_interfaces_json" in row.keys()
                else []
            ),
            dependency_policy=row["dependency_policy"] if "dependency_policy" in row.keys() else "done",
            context_policy=(
                _json_loads(row["context_policy_json"], {})
                if "context_policy_json" in row.keys()
                else {}
            ),
            verification_policy=(
                _json_loads(row["verification_policy_json"], {})
                if "verification_policy_json" in row.keys()
                else {}
            ),
            recovery_policy=(
                _json_loads(row["recovery_policy_json"], {})
                if "recovery_policy_json" in row.keys()
                else {}
            ),
            team_role_hint=row["team_role_hint"] if "team_role_hint" in row.keys() else "",
        )

    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            id=row["id"],
            run_id=row["run_id"],
            work_item_id=row["work_item_id"],
            agent_profile=row["agent_profile"],
            status=row["status"],
            attempt=row["attempt"],
            input=_json_loads(row["input_json"], {}),
            output=_json_loads(row["output_json"], {}),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=row["id"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            payload=_json_loads(row["payload_json"], {}),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_team_run(row: sqlite3.Row) -> TeamRunRecord:
        return TeamRunRecord(
            id=row["id"],
            run_id=row["run_id"],
            scope_id=row["scope_id"],
            status=row["status"],
            execution_plane=row["execution_plane"],
            work_item_ids=_json_loads(row["work_item_ids_json"], []),
            metadata=_json_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_gate(row: sqlite3.Row) -> GateRecord:
        return GateRecord(
            run_id=row["run_id"],
            gate_id=row["gate_id"],
            gate_type=row["gate_type"],
            scope_id=row["scope_id"],
            status=row["status"],
            evidence=_json_loads(row["evidence_json"], []),
            metadata=_json_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_repair_ticket(row: sqlite3.Row) -> RepairTicketRecord:
        return RepairTicketRecord(
            id=row["id"],
            run_id=row["run_id"],
            ticket_key=row["ticket_key"],
            lane=row["lane"],
            status=row["status"],
            source_gate=row["source_gate"],
            source_item_id=row["source_item_id"],
            diagnostic_fingerprint=row["diagnostic_fingerprint"],
            owner_scope=row["owner_scope"],
            owner_artifacts=_json_loads(row["owner_artifacts_json"], []),
            affected_scopes=_json_loads(row["affected_scopes_json"], []),
            conflict_keys=_json_loads(row["conflict_keys_json"], []),
            failure_summary=row["failure_summary"],
            expected_actual=row["expected_actual"],
            repair_instruction=row["repair_instruction"],
            attempt_count=int(row["attempt_count"] or 0),
            evidence_refs=_json_loads(row["evidence_refs_json"], []),
            metadata=_json_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _ensure_work_item_columns(conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(work_items)").fetchall()
        existing = {row["name"] for row in rows}
        columns = {
            "scope_id": "TEXT NOT NULL DEFAULT 'root'",
            "serial_group": "TEXT NOT NULL DEFAULT ''",
            "conflict_keys_json": "TEXT NOT NULL DEFAULT '[]'",
            "execution_mode": "TEXT NOT NULL DEFAULT 'auto'",
            "team_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "provided_interfaces_json": "TEXT NOT NULL DEFAULT '[]'",
            "required_interfaces_json": "TEXT NOT NULL DEFAULT '[]'",
            "dependency_policy": "TEXT NOT NULL DEFAULT 'done'",
            "context_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "verification_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "recovery_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "team_role_hint": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE work_items ADD COLUMN {name} {definition}")
