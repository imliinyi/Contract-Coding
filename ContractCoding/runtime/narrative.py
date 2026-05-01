"""Human-readable run narrative built from durable runtime facts."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any, Dict, Iterable, List

from ContractCoding.contract.work_item import WorkItem
from ContractCoding.contract.spec import ContractSpec
from ContractCoding.runtime.health import RunHealth
from ContractCoding.runtime.scheduler import TeamWave
from ContractCoding.runtime.store import EventRecord, GateRecord, RepairTicketRecord, RunRecord, StepRecord, TeamRunRecord


RUN_STATUS_TEXT = {
    "PENDING": "waiting to start",
    "RUNNING": "running",
    "PAUSED": "paused",
    "COMPLETED": "completed",
    "BLOCKED": "needs recovery",
    "FAILED": "automatic recovery limit reached",
    "CANCELLED": "cancelled",
}


class RunNarrativeBuilder:
    """Compress low-level run facts into a CLI-friendly story."""

    def build_report(
        self,
        *,
        run: RunRecord,
        items: List[WorkItem],
        steps: List[StepRecord],
        team_runs: List[TeamRunRecord],
        waves: List[TeamWave],
        health: RunHealth | None,
        gates: List[GateRecord] | None = None,
        repair_tickets: List[RepairTicketRecord] | None = None,
        contract: ContractSpec | None = None,
        max_lines: int = 12,
    ) -> str:
        gates = gates or []
        repair_tickets = repair_tickets or []
        counts = self._status_counts(items)
        phase = self._phase(run, items, waves, gates, team_runs, repair_tickets)
        lines = [
            f"Run {run.id}: {RUN_STATUS_TEXT.get(run.status, run.status.lower())}",
            f"Phase: {phase}",
            "Items: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())),
        ]
        milestone_summary = self._milestone_summary(contract)
        if milestone_summary:
            lines.append("Milestones: " + milestone_summary)
        phase_summary = self._phase_plan_summary(contract, gates, items)
        if phase_summary:
            lines.append("Phase plan: " + phase_summary)
        interface_summary = self._interface_summary(contract)
        if interface_summary:
            lines.append("Interfaces: " + interface_summary)

        team_gates = [gate for gate in gates if gate.gate_type == "team"]
        if team_gates:
            gate_summary = ", ".join(f"{gate.scope_id}:{gate.status}" for gate in team_gates[:6])
            if len(team_gates) > 6:
                gate_summary += f", +{len(team_gates) - 6} more"
            lines.append(f"Team gates: {gate_summary}")

        final_gate = next((gate for gate in gates if gate.gate_id == "final"), None)
        if final_gate is not None:
            lines.append(f"Final gate: {final_gate.status}")
            lines.append(f"Final readiness: {self._final_readiness(final_gate, team_gates, team_runs)}")

        failed_gates = [gate for gate in gates if gate.status in {"FAILED", "BLOCKED"}]
        if failed_gates:
            preview = ", ".join(f"{gate.gate_id}:{gate.status}" for gate in failed_gates[:4])
            lines.append(f"Failed gates: {preview}")
            diagnostics = self._diagnostic_summary(failed_gates)
            if diagnostics:
                lines.append("Unresolved diagnostics: " + diagnostics)

        active_tickets = [ticket for ticket in repair_tickets if ticket.status in {"OPEN", "RUNNING", "BLOCKED"}]
        if active_tickets:
            preview = ", ".join(
                f"{ticket.lane}:{ticket.owner_scope or ticket.source_gate}:{ticket.status}"
                for ticket in active_tickets[:4]
            )
            lines.append(f"Repair tickets: {preview}")
            blocked = [ticket for ticket in active_tickets if ticket.status == "BLOCKED"]
            if blocked:
                ticket = blocked[0]
                lines.append(
                    "Blocked repair: "
                    f"{ticket.owner_scope or ticket.source_gate} -> {ticket.failure_summary[:160]}"
                )

        coverage = self._artifact_coverage(run, items, gates, team_runs)
        if coverage:
            lines.append("Artifact coverage: " + coverage)

        if waves:
            wave = waves[0]
            reason = wave.parallel_reason or wave.serial_reason or "ready by dependency and conflict policy"
            lines.append(
                f"Next wave: {wave.wave_kind} scope={wave.scope.id} "
                f"items={len(wave.items)} slots={wave.parallel_slots}; {reason}"
            )

        recovery = self._recovery_summary(run.metadata)
        if recovery:
            lines.append("Automatic recovery: " + recovery)

        timing = self._timing_summary(steps)
        if timing:
            lines.append("Timing: " + timing)

        llm = self._llm_summary(steps)
        if llm:
            label, text = llm
            lines.append(f"{label}: {text}")

        if health is not None:
            lines.append(f"Health: {health.status}" + ("; replan recommended" if health.replan_recommended else ""))
            for diagnostic in health.diagnostics[:2]:
                lines.append(
                    f"- {self._failure_kind_text(diagnostic.failure_kind)}: {diagnostic.message} "
                    f"-> {diagnostic.recovery_action or 'observe'}"
                )

        scope_teams = [team for team in team_runs if team.metadata.get("record_type") == "scope_team"]
        if scope_teams:
            team_counts = self._team_status_counts(scope_teams)
            lines.append(
                "Teams: " + ", ".join(f"{name}={count}" for name, count in sorted(team_counts.items()))
            )
            promoted = [team.scope_id for team in scope_teams if team.status in {"PROMOTED", "CLOSED"}]
            if promoted:
                lines.append("Promoted teams: " + ", ".join(promoted[:8]))
            partial = [
                f"{team.scope_id}:{len(team.metadata.get('partial_promoted_files', []) or [])}"
                for team in scope_teams
                if team.metadata.get("partial_promoted_files")
                and team.status not in {"PROMOTED", "CLOSED"}
            ]
            if partial:
                lines.append("Partial promotions: " + ", ".join(partial[:8]) + " file(s)")
            active = [team for team in scope_teams if team.status in {"ACTIVE", "WORKSPACE_READY", "PROMOTING", "BLOCKED"}]
            if active:
                preview = ", ".join(f"{team.scope_id}:{team.status}" for team in active[:4])
                lines.append(f"Active teams: {preview}")
        elif team_runs:
            latest = team_runs[0]
            lines.append(
                f"Latest wave: {latest.status} scope={latest.scope_id} "
                f"items={','.join(latest.work_item_ids[:3])}"
            )

        return "\n".join(lines[:max_lines])

    def events_to_human(self, events: Iterable[EventRecord]) -> List[str]:
        lines: List[str] = []
        for event in reversed(list(events)):
            payload = event.payload
            prefix = f"#{event.id} {event.created_at}"
            if event.event_type == "run_status":
                lines.append(f"{prefix} run is {RUN_STATUS_TEXT.get(payload.get('status'), payload.get('status'))}.")
            elif event.event_type == "work_item_status":
                lines.append(f"{prefix} item {payload.get('id')} -> {payload.get('status')}.")
            elif event.event_type == "team_run_started":
                lines.append(
                    f"{prefix} team started for scope {payload.get('scope_id')} "
                    f"on {len(payload.get('items', []))} item(s)."
                )
            elif event.event_type == "team_run_finished":
                lines.append(f"{prefix} team {payload.get('team_id')} finished as {payload.get('status')}.")
            elif event.event_type == "team_planned":
                lines.append(
                    f"{prefix} {payload.get('scope_id')} team planned "
                    f"({payload.get('team_kind')}, {payload.get('execution_plane')})."
                )
            elif event.event_type == "team_status":
                lines.append(
                    f"{prefix} {payload.get('scope_id')} team -> {payload.get('status')}."
                )
            elif event.event_type == "gate_status":
                lines.append(
                    f"{prefix} {payload.get('gate_id')} gate -> {payload.get('status')}."
                )
            elif event.event_type == "diagnostic_recorded":
                lines.append(
                    f"{prefix} diagnostic for {payload.get('gate_id')}: "
                    f"{payload.get('failure_kind')} ({payload.get('summary')})."
                )
            elif event.event_type == "team_promoted":
                lines.append(
                    f"{prefix} {payload.get('scope_id')} team promoted "
                    f"{len(payload.get('files', []))} artifact(s)."
                )
            elif event.event_type == "team_promotion_failed":
                lines.append(
                    f"{prefix} {payload.get('scope_id')} team promotion needs recovery: {payload.get('error')}."
                )
            elif event.event_type == "dependent_teams_stale":
                lines.append(
                    f"{prefix} interface change in {payload.get('changed_scope_id')} marked "
                    f"{len(payload.get('team_ids', []))} dependent team(s) stale."
                )
            elif event.event_type == "step_finished":
                status = payload.get("status")
                label = "attempt succeeded" if status == "COMPLETED" else "attempt needs recovery"
                target = payload.get("work_item_id") or payload.get("step_id")
                timing = payload.get("timing") or {}
                timing_text = ""
                if timing:
                    timing_text = " timing=" + ", ".join(f"{key}:{value}s" for key, value in sorted(timing.items()))
                lines.append(f"{prefix} {target} {label}.{timing_text}")
            elif event.event_type in {"item_repair_requested", "infra_retry", "integration_dependency_repair_requested"}:
                lines.append(f"{prefix} automatic recovery: {event.event_type} {payload}.")
            elif event.event_type == "repair_ticket_opened":
                lines.append(
                    f"{prefix} repair ticket opened for {payload.get('owner_scope') or payload.get('source_gate')} "
                    f"({payload.get('lane')}, {payload.get('status')}): {payload.get('summary', '')}"
                )
            elif event.event_type == "repair_ticket_attempt":
                lines.append(
                    f"{prefix} repair ticket {payload.get('ticket_id')} attempt "
                    f"{payload.get('attempt_count')} on {payload.get('owner_scope')}."
                )
            elif event.event_type == "repair_ticket_status":
                lines.append(
                    f"{prefix} repair ticket {payload.get('ticket_id')} -> {payload.get('status')}."
                )
            else:
                lines.append(f"{prefix} {event.event_type}: {payload}.")
        return lines

    @staticmethod
    def _status_counts(items: List[WorkItem]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    @staticmethod
    def _team_status_counts(teams: List[TeamRunRecord]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for team in teams:
            counts[team.status] = counts.get(team.status, 0) + 1
        return counts

    @classmethod
    def _phase(
        cls,
        run: RunRecord,
        items: List[WorkItem],
        waves: List[TeamWave],
        gates: List[GateRecord],
        teams: List[TeamRunRecord],
        repair_tickets: List[RepairTicketRecord],
    ) -> str:
        if run.status == "COMPLETED":
            return "completed"
        if run.status in {"FAILED", "CANCELLED", "PAUSED"}:
            return RUN_STATUS_TEXT.get(run.status, run.status.lower())
        if any(ticket.status in {"OPEN", "RUNNING"} for ticket in repair_tickets):
            return "repairing"
        if run.status == "BLOCKED" or any(gate.status in {"FAILED", "BLOCKED"} for gate in gates):
            return "blocked" if not any(team.status == "REPAIRING" for team in teams) else "repairing"
        if any(gate.gate_id == "final" and gate.status == "RUNNING" for gate in gates):
            return "final-gating"
        if any(gate.status == "RUNNING" for gate in gates):
            return "gate-checking"
        if any(team.status == "PROMOTING" for team in teams):
            return "promoting"
        if any(team.status == "REPAIRING" for team in teams):
            return "repairing"
        if any(item.status == "BLOCKED" for item in items):
            return "repairing or waiting for recovery"
        if waves:
            return "implementing"
        return "planning"

    @staticmethod
    def _recovery_summary(metadata: Dict[str, Any]) -> str:
        counters = dict(metadata.get("autonomy_guardrails", {}))
        parts = []
        for key, label in (
            ("infra_retries", "infra retries"),
            ("item_repairs", "item repairs"),
            ("gate_repairs", "gate repairs"),
        ):
            bucket = dict(counters.get(key, {}))
            total = sum(int(value) for value in bucket.values())
            if total:
                parts.append(f"{label}={total}")
        if metadata.get("automatic_recovery_limit_reached"):
            parts.append("automatic recovery limit triggered")
        return ", ".join(parts)

    @staticmethod
    def _diagnostic_summary(gates: List[GateRecord]) -> str:
        summaries: List[str] = []
        for gate in gates:
            records = gate.metadata.get("diagnostics", [])
            if not isinstance(records, list):
                continue
            for diagnostic in records[-2:]:
                if isinstance(diagnostic, dict):
                    label = diagnostic.get("failure_kind", "diagnostic")
                    test = diagnostic.get("failing_test", "")
                    fp = diagnostic.get("fingerprint", "")
                    summaries.append(f"{gate.gate_id}:{label}{':' + test if test else ''}{' #' + fp if fp else ''}")
        return ", ".join(summaries[:4])

    @staticmethod
    def _final_readiness(final_gate: GateRecord, team_gates: List[GateRecord], teams: List[TeamRunRecord]) -> str:
        if final_gate.status == "PASSED":
            return "passed"
        unpassed_gates = [gate.scope_id for gate in team_gates if gate.status != "PASSED"]
        scope_teams = [team for team in teams if team.metadata.get("record_type") == "scope_team" and team.scope_id != "integration"]
        unpromoted = [team.scope_id for team in scope_teams if team.status not in {"PROMOTED", "CLOSED"}]
        if not unpassed_gates and not unpromoted:
            return "ready"
        parts = []
        if unpassed_gates:
            parts.append("waiting gates=" + ",".join(unpassed_gates[:6]))
        if unpromoted:
            parts.append("waiting promotion=" + ",".join(unpromoted[:6]))
        return "; ".join(parts)

    @staticmethod
    def _artifact_coverage(
        run: RunRecord,
        items: List[WorkItem],
        gates: List[GateRecord],
        team_runs: List[TeamRunRecord] | None = None,
    ) -> str:
        targets = sorted(
            {
                artifact
                for item in items
                for artifact in item.target_artifacts
                if artifact and not artifact.startswith(".contractcoding/interfaces/")
            }
        )
        final_gate = next((gate for gate in gates if gate.gate_id == "final"), None)
        if final_gate is not None:
            spec = final_gate.metadata.get("spec", {})
            required = [
                str(artifact).strip()
                for artifact in spec.get("required_artifacts", [])
                if str(artifact).strip()
            ]
            if required:
                targets = sorted(set(targets).union(required))
        if not targets:
            return ""
        generated = 0
        promoted = 0
        loc = 0
        workspace = run.workspace_dir
        team_roots = RunNarrativeBuilder._team_workspace_roots(team_runs or [])
        missing: List[str] = []
        for artifact in targets:
            path = os.path.join(workspace, artifact)
            exists_promoted = os.path.exists(path)
            exists_generated = exists_promoted or any(os.path.exists(os.path.join(root, artifact)) for root in team_roots)
            if exists_promoted:
                promoted += 1
                if artifact.endswith(".py"):
                    try:
                        with open(path, "r", encoding="utf-8") as handle:
                            loc += sum(1 for line in handle if line.strip())
                    except OSError:
                        pass
            if exists_generated:
                generated += 1
            else:
                missing.append(artifact)
        text = (
            f"required={len(targets)}, generated_sandbox_or_main={generated}, "
            f"promoted_main={promoted}, promoted_python_loc={loc}"
        )
        if missing:
            text += "; missing=" + ", ".join(missing[:6])
            if len(missing) > 6:
                text += f", +{len(missing) - 6} more"
        return text

    @staticmethod
    def _team_workspace_roots(team_runs: List[TeamRunRecord]) -> List[str]:
        roots: List[str] = []
        for team in team_runs:
            plane = dict(team.metadata.get("plane", {})) if isinstance(team.metadata, dict) else {}
            root = str(plane.get("root_dir", "") or plane.get("workspace_dir", "") or "").strip()
            if root and root not in roots:
                roots.append(root)
        return roots

    @staticmethod
    def _milestone_summary(contract: ContractSpec | None) -> str:
        if contract is None or not contract.milestones:
            return ""
        current = next((milestone for milestone in contract.milestones if milestone.status not in {"DONE", "PASSED"}), contract.milestones[-1])
        return f"{current.id} ({current.mode})"

    @staticmethod
    def _phase_plan_summary(
        contract: ContractSpec | None,
        gates: List[GateRecord],
        items: List[WorkItem],
    ) -> str:
        if contract is None or not contract.phase_plan:
            return ""
        gate_by_id = {gate.gate_id: gate for gate in gates}
        status_by_item = {item.id: item.status for item in items}
        completed = 0
        current = contract.phase_plan[-1]
        for phase in contract.phase_plan:
            gate = gate_by_id.get(f"phase:{phase.phase_id}")
            phase_items = [
                item
                for item in contract.work_items
                if str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")) == phase.phase_id
            ]
            item_done = not phase_items or all(status_by_item.get(item.id, item.status) == "VERIFIED" for item in phase_items)
            if gate is not None and gate.status == "PASSED":
                completed += 1
                continue
            if (
                not phase_items
                and phase.phase_id in {"requirements.freeze", "architecture.sketch", "critical.interfaces"}
                and (gate is None or gate.status in {"PENDING", "RUNNING"})
            ):
                completed += 1
                continue
            if gate is None and item_done:
                completed += 1
                continue
            current = phase
            break
        return f"{completed}/{len(contract.phase_plan)} complete; current={current.phase_id} ({current.mode})"

    @staticmethod
    def _interface_summary(contract: ContractSpec | None) -> str:
        if contract is None or not contract.interfaces:
            return ""
        critical = [interface for interface in contract.interfaces if interface.critical]
        frozen_critical = [interface for interface in critical if interface.status in {"FROZEN", "IMPLEMENTED", "VERIFIED"}]
        team = [interface for interface in contract.interfaces if not interface.critical]
        frozen_team = [interface for interface in team if interface.status in {"FROZEN", "IMPLEMENTED", "VERIFIED"}]
        parts = []
        if critical:
            parts.append(f"critical={len(frozen_critical)}/{len(critical)} frozen")
        if team:
            parts.append(f"team={len(frozen_team)}/{len(team)} frozen")
        return ", ".join(parts)

    @staticmethod
    def _timing_summary(steps: List[StepRecord]) -> str:
        totals: Dict[str, float] = {}
        slowest: tuple[str, float] = ("", 0.0)
        for step in steps:
            timing = dict(step.output.get("timing", {})) if isinstance(step.output, dict) else {}
            for key, value in timing.items():
                try:
                    seconds = float(value)
                except (TypeError, ValueError):
                    continue
                totals[key] = totals.get(key, 0.0) + seconds
                if seconds > slowest[1]:
                    slowest = (f"{step.work_item_id}:{key}", seconds)
        if not totals:
            elapsed = RunNarrativeBuilder._elapsed_summary(steps)
            return elapsed
        top = ", ".join(f"{key}={value:.1f}s" for key, value in sorted(totals.items())[:4])
        if slowest[0]:
            top += f"; slowest={slowest[0]} {slowest[1]:.1f}s"
        return top

    @staticmethod
    def _elapsed_summary(steps: List[StepRecord]) -> str:
        durations: List[tuple[str, float]] = []
        for step in steps:
            try:
                start = datetime.fromisoformat(step.created_at)
                end = datetime.fromisoformat(step.updated_at)
            except ValueError:
                continue
            seconds = max(0.0, (end - start).total_seconds())
            durations.append((step.work_item_id, seconds))
        if not durations:
            return ""
        slowest = max(durations, key=lambda item: item[1])
        total = sum(duration for _, duration in durations)
        return f"recent steps total={total:.1f}s; slowest={slowest[0]} {slowest[1]:.1f}s"

    @staticmethod
    def _llm_summary(steps: List[StepRecord]) -> tuple[str, str] | None:
        backends = set()
        event_count = 0
        timeouts = 0
        empty = 0
        tool_intents = 0
        tool_results = 0
        prompt_tokens = 0
        completion_tokens = 0
        touched = 0
        for step in steps:
            if not isinstance(step.output, dict):
                continue
            observed = dict(step.output.get("llm_observability", {}) or {})
            if not observed:
                continue
            touched += 1
            backends.add(str(observed.get("backend", "unknown") or "unknown"))
            event_count += int(observed.get("event_count", 0) or 0)
            timeouts += int(observed.get("timeout_count", 0) or 0)
            empty += int(observed.get("empty_response_count", 0) or 0)
            tool_intents += int(observed.get("tool_intent_count", 0) or 0)
            tool_results += int(observed.get("tool_result_count", 0) or 0)
            prompt_tokens += int(observed.get("prompt_tokens", 0) or 0)
            completion_tokens += int(observed.get("completion_tokens", 0) or 0)
        if not touched:
            return None
        parts = [f"observed_steps={touched}", f"events={event_count}"]
        if tool_intents:
            parts.append(f"tool_intents={tool_intents}")
        if tool_results:
            parts.append(f"tool_results={tool_results}")
        if prompt_tokens or completion_tokens:
            parts.append(f"tokens={prompt_tokens}/{completion_tokens}")
        if timeouts:
            parts.append(f"timeouts={timeouts}")
        if empty:
            parts.append(f"empty_outputs={empty}")
        label = "LLM"
        if backends:
            parts.insert(0, "backends=" + ",".join(sorted(backends)))
        return label, ", ".join(parts)

    @staticmethod
    def _failure_kind_text(kind: str) -> str:
        return {
            "infra": "infrastructure",
            "item_quality": "implementation/test",
            "contract_plan": "contract planning",
            "human_required": "needs human",
        }.get(kind or "", "diagnostic")
