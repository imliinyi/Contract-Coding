"""First-class scope team runtime for long-running runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import shutil
from typing import Any, Dict, Iterable, List, Optional, Set

from ContractCoding.config import Config
from ContractCoding.contract.spec import ContractSpec, WorkScope
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.execution.planes import ExecutionPlane, ExecutionPlaneManager
from ContractCoding.runtime.promotion import PromotionMetadataWriter
from ContractCoding.runtime.store import RunRecord, RunStore, TeamRunRecord


TEAM_LIFECYCLE_STATUSES = {
    "PLANNED",
    "WORKSPACE_READY",
    "ACTIVE",
    "LOCAL_VERIFIED",
    "PROMOTING",
    "PROMOTED",
    "CLOSED",
    "BLOCKED",
    "REPAIRING",
    "STALE_DEPENDENCY",
}


@dataclass
class TeamMemory:
    scope_brief: str = ""
    interface_decisions: List[str] = field(default_factory=list)
    local_failures: List[str] = field(default_factory=list)
    verified_evidence: List[str] = field(default_factory=list)
    pending_risks: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "scope_brief": self.scope_brief,
            "interface_decisions": list(self.interface_decisions),
            "local_failures": list(self.local_failures),
            "verified_evidence": list(self.verified_evidence),
            "pending_risks": list(self.pending_risks),
        }

    @classmethod
    def from_record(cls, value: Dict[str, Any] | None) -> "TeamMemory":
        value = dict(value or {})
        return cls(
            scope_brief=str(value.get("scope_brief", "")),
            interface_decisions=[str(item) for item in value.get("interface_decisions", [])],
            local_failures=[str(item) for item in value.get("local_failures", [])],
            verified_evidence=[str(item) for item in value.get("verified_evidence", [])],
            pending_risks=[str(item) for item in value.get("pending_risks", [])],
        )


@dataclass
class TeamSpec:
    team_id: str
    scope_id: str
    team_kind: str
    workspace_plane: str
    roles: List[str]
    owned_items: List[str]
    owned_artifacts: List[str]
    conflict_keys: List[str]
    promotion_policy: Dict[str, Any] = field(default_factory=dict)
    team_memory: TeamMemory = field(default_factory=TeamMemory)
    status: str = "PLANNED"

    def to_record(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "scope_id": self.scope_id,
            "team_kind": self.team_kind,
            "workspace_plane": self.workspace_plane,
            "roles": list(self.roles),
            "owned_items": list(self.owned_items),
            "owned_artifacts": list(self.owned_artifacts),
            "conflict_keys": list(self.conflict_keys),
            "promotion_policy": dict(self.promotion_policy),
            "team_memory": self.team_memory.to_record(),
            "status": self.status,
        }


class TeamPlanner:
    """Materialize one durable team spec for each non-root work scope."""

    def materialize(self, contract: ContractSpec) -> List[TeamSpec]:
        items_by_scope: Dict[str, List[WorkItem]] = {}
        for item in contract.work_items:
            items_by_scope.setdefault(item.scope_id or "root", []).append(item)
        gate_artifacts_by_scope: Dict[str, List[str]] = {
            gate.scope_id: list(gate.test_artifacts)
            for gate in contract.team_gates
        }

        specs: List[TeamSpec] = []
        for scope in contract.work_scopes:
            if scope.id in {"root", "integration"} or scope.type == "integration":
                continue
            owned_items = sorted(items_by_scope.get(scope.id, []), key=lambda item: item.id)
            if not owned_items and not scope.artifacts:
                continue
            team_kind = self._team_kind(scope, owned_items)
            workspace_plane = self._workspace_plane(scope, team_kind, owned_items, contract)
            owned_artifacts = sorted(
                {
                    artifact
                    for artifact in [
                        *scope.artifacts,
                        *gate_artifacts_by_scope.get(scope.id, []),
                        *[target for item in owned_items for target in item.target_artifacts],
                    ]
                    if artifact and not self._is_runtime_ledger_artifact(artifact)
                }
            )
            conflict_keys = sorted(
                {
                    key
                    for item in owned_items
                    for key in (item.conflict_keys or [f"artifact:{artifact}" for artifact in item.target_artifacts])
                    if key
                }
                | {f"artifact:{artifact}" for artifact in gate_artifacts_by_scope.get(scope.id, [])}
                or set(scope.conflict_keys)
            )
            specs.append(
                TeamSpec(
                    team_id=f"team:{scope.id}",
                    scope_id=scope.id,
                    team_kind=team_kind,
                    workspace_plane=workspace_plane,
                    roles=self._roles_for(team_kind),
                    owned_items=[item.id for item in owned_items],
                    owned_artifacts=owned_artifacts,
                    conflict_keys=conflict_keys,
                    promotion_policy=dict(scope.promotion_policy or self._default_promotion_policy(team_kind)),
                    team_memory=TeamMemory(scope_brief=self._scope_brief(scope, owned_items)),
                    status="PLANNED",
                )
            )
        return specs

    @staticmethod
    def _team_kind(scope: WorkScope, items: List[WorkItem]) -> str:
        policy_kind = str((scope.team_policy or {}).get("team_kind", "")).strip().lower()
        if policy_kind:
            return policy_kind
        if scope.type in {"integration", "eval"}:
            return "integration"
        kinds = {item.kind for item in items}
        if kinds == {"research"}:
            return "research"
        if kinds and kinds <= {"doc"}:
            return "doc"
        if kinds and kinds <= {"data"}:
            return "data"
        if "ops" in kinds:
            return "ops"
        if scope.type == "tests":
            return "tests"
        return "coding"

    @staticmethod
    def _workspace_plane(scope: WorkScope, team_kind: str, items: List[WorkItem], contract: ContractSpec) -> str:
        policy_plane = str((scope.team_policy or {}).get("workspace_plane", "")).strip().lower()
        if policy_plane:
            return policy_plane
        if scope.execution_plane_policy and scope.execution_plane_policy != "auto":
            return scope.execution_plane_policy
        if team_kind in {"coding", "tests"} or any(item.kind == "coding" for item in items):
            return "worktree"
        if team_kind == "research":
            return "read-only"
        if team_kind == "ops":
            return "approval-required"
        if team_kind == "integration":
            return "workspace"
        return str(contract.execution_policy.get("default_execution_plane", "sandbox") or "sandbox")

    @staticmethod
    def _roles_for(team_kind: str) -> List[str]:
        if team_kind in {"coding", "tests"}:
            return ["Team Lead", "Implementation Worker", "Test Worker", "Reviewer", "System Verifier"]
        if team_kind == "research":
            return ["Team Lead", "Researcher", "Claim Verifier"]
        if team_kind in {"doc", "paper"}:
            return ["Team Lead", "Writer", "Consistency Verifier"]
        if team_kind == "data":
            return ["Team Lead", "Data Worker", "Data Verifier"]
        if team_kind == "ops":
            return ["Team Lead", "Operator", "Approval Governor"]
        return ["Integrator", "System Verifier"]

    @staticmethod
    def _scope_brief(scope: WorkScope, items: List[WorkItem]) -> str:
        artifact_count = len({*scope.artifacts, *[artifact for item in items for artifact in item.target_artifacts]})
        return f"{scope.label or scope.id}: {len(items)} item(s), {artifact_count} artifact(s)."

    @staticmethod
    def _default_promotion_policy(team_kind: str) -> Dict[str, Any]:
        if team_kind in {"integration", "research"}:
            return {"mode": "none"}
        return {"mode": "after_team_gate"}

    @staticmethod
    def _is_runtime_ledger_artifact(path: str) -> bool:
        normalized = str(path).replace("\\", "/")
        return (
            normalized.startswith(".contractcoding/runs.sqlite")
            or normalized.startswith(".contractcoding/events")
            or normalized.startswith(".contractcoding/interfaces/")
            or normalized == ".contractcoding/integration_report.json"
        )


class TeamRuntime:
    """Own team workspace lifecycle, local verification state, and promotion."""

    def __init__(
        self,
        config: Config,
        store: RunStore,
        plane_manager: Optional[ExecutionPlaneManager] = None,
        planner: Optional[TeamPlanner] = None,
    ):
        self.config = config
        self.store = store
        self.plane_manager = plane_manager or ExecutionPlaneManager(config)
        self.planner = planner or TeamPlanner()
        self._planes: Dict[str, ExecutionPlane] = {}

    def ensure_teams(self, run_id: str, contract: ContractSpec) -> List[TeamRunRecord]:
        records: List[TeamRunRecord] = []
        for spec in self.planner.materialize(contract):
            records.append(self.store.ensure_scope_team_run(run_id, spec.to_record()))
        return records

    def team_for_scope(self, run_id: str, scope_id: str, contract: Optional[ContractSpec] = None) -> Optional[TeamRunRecord]:
        team = self.store.get_scope_team_run(run_id, scope_id)
        if team is not None:
            return team
        if contract is not None:
            self.ensure_teams(run_id, contract)
            return self.store.get_scope_team_run(run_id, scope_id)
        return None

    def spec_for_scope(self, contract: ContractSpec, scope_id: str) -> Optional[TeamSpec]:
        return next((spec for spec in self.planner.materialize(contract) if spec.scope_id == scope_id), None)

    def ensure_workspace(self, run: RunRecord, contract: ContractSpec, scope_id: str) -> str:
        spec = self.spec_for_scope(contract, scope_id)
        team = self.team_for_scope(run.id, scope_id, contract)
        if spec is None or team is None:
            return os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
        if team.metadata.get("revalidation_only"):
            return os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
        if not self._uses_isolated_workspace(spec):
            if team.status == "PLANNED":
                self.store.update_team_run_status(team.id, "WORKSPACE_READY")
            return os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)

        plane = self._plane_for(run, spec, team)
        self._sync_system_artifacts(run, contract, spec, team, plane.working_dir)
        self._sync_promoted_dependency_artifacts(run, contract, spec, team, plane.working_dir)
        if team.status == "PLANNED":
            self.store.update_team_run_status(
                team.id,
                "WORKSPACE_READY",
                {
                    "plane": self._plane_to_record(plane),
                    "system_artifacts_synced": True,
                },
            )
        return plane.working_dir

    def mark_active(self, team_id: str, work_item_ids: Iterable[str]) -> None:
        team = self.store.get_team_run(team_id)
        metadata = {"active_items": [str(item_id) for item_id in work_item_ids]}
        if team and team.status == "STALE_DEPENDENCY":
            metadata["previous_status"] = team.status
        self.store.update_team_run_status(team_id, "ACTIVE", metadata)

    def brief_team_lead(
        self,
        run_id: str,
        contract: ContractSpec,
        scope_id: str,
        work_items: Iterable[WorkItem],
    ) -> None:
        """Record a deterministic Team Lead brief before local dispatch.

        The Team Lead is a runtime coordination role, not a contract author.
        This brief gives the team a durable local memory: scope intent,
        current dispatch, owned artifacts, dependency posture, and likely risks.
        """

        team = self.team_for_scope(run_id, scope_id, contract)
        scope = contract.scope_by_id().get(scope_id)
        if team is None or scope is None:
            return
        existing_memory = TeamMemory.from_record(team.metadata.get("team_memory", {}))
        items = list(work_items)
        item_ids = [item.id for item in items]
        owned_artifacts = sorted({artifact for item in items for artifact in item.target_artifacts})
        risks = self._lead_risks(items)
        interface_notes = self._interface_notes(items)
        brief = (
            f"Team Lead brief for `{scope_id}`: {scope.label}; "
            f"dispatch={item_ids}; artifacts={owned_artifacts or ['none']}; "
            f"risks={risks or ['none']}."
        )
        scope_items = [item for item in contract.work_items if item.scope_id == scope_id]
        memory = TeamMemory(
            scope_brief=existing_memory.scope_brief or TeamPlanner._scope_brief(scope, scope_items),
            interface_decisions=[*existing_memory.interface_decisions, *interface_notes][-10:],
            local_failures=list(existing_memory.local_failures)[-10:],
            verified_evidence=list(existing_memory.verified_evidence)[-10:],
            pending_risks=[*existing_memory.pending_risks, *risks][-10:],
        )
        dispatch_history = list(team.metadata.get("lead_dispatch_history", []))
        dispatch_history.append({"items": item_ids, "artifacts": owned_artifacts, "risks": risks})
        self.store.update_team_run_status(
            team.id,
            team.status,
            {
                "team_memory": memory.to_record(),
                "lead_brief": brief,
                "lead_briefed": True,
                "lead_dispatch_history": dispatch_history[-20:],
            },
        )
        self.store.append_event(
            run_id,
            "team_lead_briefed",
            {
                "team_id": team.id,
                "scope_id": scope_id,
                "items": item_ids,
                "artifacts": owned_artifacts,
                "risks": risks,
            },
        )

    def mark_wave_result(self, team_id: str, completed: Iterable[str], failed: Dict[str, str]) -> None:
        if failed:
            self.store.update_team_run_status(
                team_id,
                "BLOCKED",
                {"last_failed_items": dict(failed)},
            )
            return
        self.store.update_team_run_status(team_id, "ACTIVE", {"last_completed_items": list(completed)})

    @staticmethod
    def _lead_risks(items: List[WorkItem]) -> List[str]:
        risks: List[str] = []
        if any(item.kind == "ops" for item in items):
            risks.append("ops approval/dry-run required")
        if any(item.dependency_policy == "interface" for item in items):
            risks.append("depends on stable interface rather than verified implementation")
        serial_groups = sorted({item.serial_group for item in items if item.serial_group})
        if serial_groups:
            risks.append(f"serial groups: {', '.join(serial_groups)}")
        return risks

    @staticmethod
    def _interface_notes(items: List[WorkItem]) -> List[str]:
        notes: List[str] = []
        for item in items:
            if item.provided_interfaces:
                notes.append(f"{item.id} provides {len(item.provided_interfaces)} interface record(s)")
            if item.required_interfaces:
                notes.append(f"{item.id} requires {len(item.required_interfaces)} interface record(s)")
        return notes

    def promote_if_ready(self, run: RunRecord, contract: ContractSpec, scope_id: str) -> bool:
        spec = self.spec_for_scope(contract, scope_id)
        team = self.team_for_scope(run.id, scope_id, contract)
        if spec is None or team is None or team.status in {"PROMOTED", "CLOSED"}:
            return False
        if not self._scope_locally_verified(run.id, spec):
            return False

        self.store.update_team_run_status(team.id, "LOCAL_VERIFIED")
        if team.metadata.get("revalidation_only"):
            self.store.update_team_run_status(
                team.id,
                "CLOSED",
                {
                    "revalidated_after_dependency": team.metadata.get("stale_dependency", ""),
                    "revalidation_only": False,
                },
            )
            self.store.append_event(
                run.id,
                "team_revalidated",
                {
                    "team_id": team.id,
                    "scope_id": scope_id,
                    "stale_dependency": team.metadata.get("stale_dependency", ""),
                },
            )
            return True
        mode = str(spec.promotion_policy.get("mode", "after_team_gate"))
        if mode == "none":
            self.store.update_team_run_status(team.id, "CLOSED", {"promotion": "not-required"})
            return True

        if not self._uses_isolated_workspace(spec):
            self.store.update_team_run_status(team.id, "PROMOTED", {"promotion": "workspace"})
            self.store.update_team_run_status(team.id, "CLOSED")
            return True

        plane = self._plane_for(run, spec, team)
        changed_files = self._promotable_files(run.id, spec, plane.working_dir)
        self.store.update_team_run_status(team.id, "PROMOTING", {"promoting_files": sorted(changed_files)})
        promotion_metadata_path = ""
        try:
            self._refresh_late_owned_file_baselines(run, spec, team, plane, changed_files)
            promoted = self._promote_owned_files(run, spec, team, plane, changed_files)
            remaining = changed_files - promoted
            if remaining:
                promoted.update(self.plane_manager.promote(plane, remaining))
            if bool(getattr(self.config, "PROMOTION_PATCH_SUMMARY", True)):
                promotion_metadata_path = self._write_promotion_metadata(
                    run=run,
                    spec=spec,
                    team=team,
                    plane=plane,
                    changed_files=changed_files,
                    promoted_files=promoted,
                )
        except Exception as exc:
            if bool(getattr(self.config, "PROMOTION_PATCH_SUMMARY", True)):
                try:
                    promotion_metadata_path = self._write_promotion_metadata(
                        run=run,
                        spec=spec,
                        team=team,
                        plane=plane,
                        changed_files=changed_files,
                        promoted_files=[],
                        conflict_reason=str(exc),
                    )
                except Exception:
                    promotion_metadata_path = ""
            self.store.update_team_run_status(team.id, "BLOCKED", {"promotion_error": str(exc)})
            self.store.append_event(
                run.id,
                "team_promotion_failed",
                {"team_id": team.id, "scope_id": scope_id, "error": str(exc), "promotion_metadata": promotion_metadata_path},
            )
            return False
        self.store.update_team_run_status(
            team.id,
            "PROMOTED",
            {
                "promoted_files": sorted(promoted),
                "plane": self._plane_to_record(plane),
                "promotion_metadata": promotion_metadata_path,
            },
        )
        self.store.append_event(
            run.id,
            "team_promoted",
            {"team_id": team.id, "scope_id": scope_id, "files": sorted(promoted), "promotion_metadata": promotion_metadata_path},
        )
        self.plane_manager.cleanup(plane)
        self._planes.pop(team.id, None)
        self.store.update_team_run_status(team.id, "CLOSED")
        return True

    def promote_verified_artifacts(self, run: RunRecord, contract: ContractSpec, scope_id: str) -> Set[str]:
        """Promote verified owner artifacts before the whole team gate closes.

        Long-running runs need the main workspace to receive safe, already
        self-checked slices while later phase work is still queued. This is a
        smoke-level handoff, not final acceptance: the team gate and final gate
        still decide whether the complete scope is done.
        """

        if not bool(getattr(self.config, "CONTRACTCODING_PARTIAL_PROMOTION", True)):
            return set()
        spec = self.spec_for_scope(contract, scope_id)
        team = self.team_for_scope(run.id, scope_id, contract)
        if spec is None or team is None or team.status in {"PROMOTED", "CLOSED", "BLOCKED"}:
            return set()
        mode = str(spec.promotion_policy.get("mode", "after_team_gate"))
        if mode == "none" or not self._uses_isolated_workspace(spec):
            return set()

        candidate_files = self._verified_item_candidate_files(run.id, spec)
        if not candidate_files:
            return set()
        plane = self._existing_plane_for_partial_promotion(team)
        if plane is None:
            return set()
        already = {
            self._normalize_path(path)
            for path in [
                *list(team.metadata.get("partial_promoted_files", []) or []),
                *list(team.metadata.get("promoted_files", []) or []),
            ]
            if str(path).strip()
        }
        changed_files = self._verified_item_promotable_files(run.id, spec, plane.working_dir, candidate_files) - already
        if not changed_files:
            return set()

        try:
            self._refresh_late_owned_file_baselines(run, spec, team, plane, changed_files)
            promoted = self._promote_owned_files(run, spec, team, plane, changed_files)
            if not promoted:
                return set()
            partial_promoted = sorted(already | promoted)
            metadata: Dict[str, Any] = {
                "partial_promoted_files": partial_promoted,
                "last_partial_promoted_files": sorted(promoted),
                "partial_promotion": "verified_artifacts",
            }
            if bool(getattr(self.config, "PROMOTION_PATCH_SUMMARY", True)):
                metadata["partial_promotion_metadata"] = self._write_promotion_metadata(
                    run=run,
                    spec=spec,
                    team=team,
                    plane=plane,
                    changed_files=promoted,
                    promoted_files=promoted,
                )
            self.store.update_team_run_status(team.id, team.status, metadata)
            self.store.append_event(
                run.id,
                "team_partial_promoted",
                {"team_id": team.id, "scope_id": spec.scope_id, "files": sorted(promoted)},
            )
            return promoted
        except Exception as exc:
            self.store.append_event(
                run.id,
                "team_partial_promotion_failed",
                {"team_id": team.id, "scope_id": spec.scope_id, "error": str(exc)},
            )
            return set()

    def _write_promotion_metadata(
        self,
        *,
        run: RunRecord,
        spec: TeamSpec,
        team: TeamRunRecord,
        plane: ExecutionPlane,
        changed_files: Set[str],
        promoted_files: Iterable[str],
        conflict_reason: str = "",
    ) -> str:
        writer = PromotionMetadataWriter(plane.base_workspace_dir)
        summary = writer.build(
            run_id=run.id,
            team_id=team.id,
            scope_id=spec.scope_id,
            working_dir=plane.working_dir,
            base_dir=plane.base_workspace_dir,
            changed_files=changed_files,
            owned_artifacts=spec.owned_artifacts,
            promoted_files=promoted_files,
            conflict_reason=conflict_reason,
        )
        path = writer.write(summary)
        self.store.append_event(
            run.id,
            "promotion_metadata_written",
            {
                "team_id": team.id,
                "scope_id": spec.scope_id,
                "path": path,
                "changed_files": summary.changed_files,
                "owned_files": summary.owned_files,
                "unowned_files": summary.unowned_files,
                "conflict_reason": summary.conflict_reason,
            },
        )
        return path

    def _promote_owned_files(
        self,
        run: RunRecord,
        spec: TeamSpec,
        team: TeamRunRecord,
        plane: ExecutionPlane,
        changed_files: Set[str],
    ) -> Set[str]:
        """Promote contract-owned artifacts with owner-wins semantics.

        A functional team is the single writer for its owned artifacts. Once
        the team gate has passed, its workspace version is authoritative for
        those files; using a textual three-way merge can reintroduce stale
        conflicts from previous repairs by the same owner.
        """

        if not plane.isolated:
            return set(changed_files)
        owned = {self._normalize_path(path) for path in spec.owned_artifacts}
        promoted: Set[str] = set()
        for rel_path in sorted(changed_files):
            normalized = self._normalize_path(rel_path)
            if normalized not in owned:
                continue
            source = os.path.join(plane.working_dir, normalized)
            dest = os.path.join(plane.base_workspace_dir, normalized)
            if not os.path.isfile(source):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(source, dest)
            promoted.add(normalized)
        if promoted:
            self.store.append_event(
                run.id,
                "owned_artifacts_promoted",
                {
                    "team_id": team.id,
                    "scope_id": spec.scope_id,
                    "artifacts": sorted(promoted),
                },
            )
        return promoted

    def _refresh_late_owned_file_baselines(
        self,
        run: RunRecord,
        spec: TeamSpec,
        team: TeamRunRecord,
        plane: ExecutionPlane,
        changed_files: Set[str],
    ) -> None:
        """Let the contract owner promote a file that appeared after plane creation.

        Isolated team planes snapshot the main workspace at acquisition time.
        In phase-driven runs a package/root artifact can be created or repaired
        after other teams have already promoted dependent code. If the file is
        still owned by this team, the contract ownership boundary is the safety
        mechanism; promotion should compare against the current main file rather
        than failing because the original baseline did not contain it.
        """

        if not plane.isolated or not plane.baseline_dir:
            return
        hashes = plane.baseline_hashes if plane.baseline_hashes is not None else {}
        owned = {self._normalize_path(path) for path in spec.owned_artifacts}
        refreshed: List[str] = []
        for rel_path in sorted(changed_files):
            normalized = self._normalize_path(rel_path)
            if not normalized or normalized not in owned:
                continue
            if hashes.get(normalized) is not None:
                continue
            dest = os.path.join(plane.base_workspace_dir, normalized)
            source = os.path.join(plane.working_dir, normalized)
            baseline = os.path.join(plane.baseline_dir, normalized)
            if not os.path.isfile(dest) or not os.path.isfile(source):
                continue
            os.makedirs(os.path.dirname(baseline), exist_ok=True)
            try:
                shutil.copy2(dest, baseline)
            except OSError as exc:
                self.store.append_event(
                    run.id,
                    "late_owned_baseline_refresh_failed",
                    {
                        "team_id": team.id,
                        "scope_id": spec.scope_id,
                        "artifact": normalized,
                        "error": str(exc),
                    },
                )
                continue
            hashes[normalized] = self._file_digest(dest)
            refreshed.append(normalized)
        if refreshed:
            plane.baseline_hashes = hashes
            self._planes[team.id] = plane
            self.store.update_team_run_status(team.id, "PROMOTING", {"plane": self._plane_to_record(plane)})
            self.store.append_event(
                run.id,
                "late_owned_baselines_refreshed",
                {
                    "team_id": team.id,
                    "scope_id": spec.scope_id,
                    "artifacts": refreshed,
                },
            )

    def _scope_locally_verified(self, run_id: str, spec: TeamSpec) -> bool:
        gate = self.store.get_gate(run_id, f"team:{spec.scope_id}")
        if gate is not None:
            return gate.status == "PASSED"
        items = {item.id: item for item in self.store.list_work_items(run_id)}
        owned = [items[item_id] for item_id in spec.owned_items if item_id in items]
        relevant = [item for item in owned if not item.id.startswith(("interface:", "scaffold:"))]
        return bool(relevant) and all(item.status == "VERIFIED" for item in relevant)

    def _promotable_files(self, run_id: str, spec: TeamSpec, workspace_dir: str) -> Set[str]:
        items = {item.id: item for item in self.store.list_work_items(run_id)}
        files: Set[str] = set(spec.owned_artifacts)
        for item_id in spec.owned_items:
            item = items.get(item_id)
            if item and item.status in {"DONE", "VERIFIED"}:
                files.update(item.target_artifacts)
        out: Set[str] = set()
        for rel_path in files:
            normalized = self._normalize_path(rel_path)
            if (
                not normalized
                or TeamPlanner._is_runtime_ledger_artifact(normalized)
                or self._is_system_contract_artifact(normalized)
            ):
                continue
            if os.path.exists(os.path.join(workspace_dir, normalized)):
                out.add(normalized)
        return out

    def _verified_item_candidate_files(self, run_id: str, spec: TeamSpec) -> Set[str]:
        items = {item.id: item for item in self.store.list_work_items(run_id)}
        owned = {self._normalize_path(path) for path in spec.owned_artifacts}
        out: Set[str] = set()
        for item_id in spec.owned_items:
            item = items.get(item_id)
            if item is None or item.status not in {"DONE", "VERIFIED"}:
                continue
            if item.id.startswith(("interface:", "scaffold:")):
                continue
            for rel_path in item.target_artifacts:
                normalized = self._normalize_path(rel_path)
                if (
                    not normalized
                    or normalized not in owned
                    or TeamPlanner._is_runtime_ledger_artifact(normalized)
                    or self._is_system_contract_artifact(normalized)
                ):
                    continue
                out.add(normalized)
        return out

    def _verified_item_promotable_files(
        self,
        run_id: str,
        spec: TeamSpec,
        workspace_dir: str,
        candidate_files: Optional[Set[str]] = None,
    ) -> Set[str]:
        candidates = candidate_files if candidate_files is not None else self._verified_item_candidate_files(run_id, spec)
        out: Set[str] = set()
        for normalized in candidates:
            if os.path.exists(os.path.join(workspace_dir, normalized)):
                out.add(normalized)
        return out

    def _existing_plane_for_partial_promotion(self, team: TeamRunRecord) -> Optional[ExecutionPlane]:
        cached = self._planes.get(team.id)
        if cached is not None and os.path.isdir(cached.working_dir):
            return cached
        plane_record = dict(team.metadata.get("plane", {}))
        if plane_record and os.path.isdir(str(plane_record.get("working_dir", ""))):
            plane = self._plane_from_record(plane_record)
            self._planes[team.id] = plane
            return plane
        return None

    @staticmethod
    def _is_system_contract_artifact(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/")
        return normalized.startswith(
            (
                ".contractcoding/interfaces/",
                ".contractcoding/interface_tests/",
                ".contractcoding/scaffolds/",
            )
        ) or normalized in {
            ".contractcoding/contract.json",
            ".contractcoding/contract.md",
            ".contractcoding/prd.md",
        }

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    def _plane_for(self, run: RunRecord, spec: TeamSpec, team: TeamRunRecord) -> ExecutionPlane:
        cached = self._planes.get(team.id)
        if cached is not None and os.path.isdir(cached.working_dir):
            return cached
        plane_record = dict(team.metadata.get("plane", {}))
        if plane_record and os.path.isdir(str(plane_record.get("working_dir", ""))):
            plane = self._plane_from_record(plane_record)
            self._planes[team.id] = plane
            return plane

        plane = self.plane_manager.acquire(
            module_name=spec.scope_id,
            isolated=True,
            mode_override=spec.workspace_plane,
        )
        self._planes[team.id] = plane
        self.store.update_team_run_status(team.id, "WORKSPACE_READY", {"plane": self._plane_to_record(plane)})
        return plane

    def _sync_system_artifacts(
        self,
        run: RunRecord,
        contract: ContractSpec,
        spec: TeamSpec,
        team: TeamRunRecord,
        workspace_dir: str,
    ) -> None:
        """Copy compiler-owned contract artifacts into an isolated team plane.

        These files are generated after some team workspaces are created, so a
        one-time baseline snapshot is not enough for long-running phase work.
        Runtime ledgers are intentionally excluded.
        """

        source_root = os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
        target_root = os.path.abspath(workspace_dir)
        if not source_root or not target_root or source_root == target_root:
            return
        copied: List[str] = []
        for rel_path in self._system_artifact_paths(source_root):
            source = os.path.join(source_root, rel_path)
            target = os.path.join(target_root, rel_path)
            if not os.path.isfile(source):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                self.store.append_event(
                    run.id,
                    "system_artifact_sync_failed",
                    {
                        "team_id": team.id,
                        "scope_id": spec.scope_id,
                        "artifact": rel_path,
                        "error": str(exc),
                    },
                )
                continue
            copied.append(rel_path)
        if copied:
            self.store.update_team_run_status(
                team.id,
                team.status,
                {
                    "system_artifacts_synced": True,
                    "system_artifacts": copied[-50:],
                },
            )
            self.store.append_event(
                run.id,
                "system_artifacts_synced",
                {
                    "team_id": team.id,
                    "scope_id": spec.scope_id,
                    "count": len(copied),
                },
            )

    def _sync_promoted_dependency_artifacts(
        self,
        run: RunRecord,
        contract: ContractSpec,
        spec: TeamSpec,
        team: TeamRunRecord,
        workspace_dir: str,
    ) -> None:
        """Refresh a team plane with promoted non-owned production code."""

        source_root = os.path.abspath(run.workspace_dir or self.config.WORKSPACE_DIR)
        target_root = os.path.abspath(workspace_dir)
        if not source_root or not target_root or source_root == target_root:
            return
        owned = {self._normalize_path(path) for path in spec.owned_artifacts}
        for gate in contract.team_gates:
            if gate.scope_id == spec.scope_id:
                owned.update(self._normalize_path(path) for path in gate.test_artifacts)
        package_roots = set(contract.final_gate.package_roots if contract.final_gate else [])
        if not package_roots:
            package_roots = {
                artifact.split("/", 1)[0]
                for scope in contract.work_scopes
                for artifact in scope.artifacts
                if "/" in artifact and artifact.endswith(".py") and not artifact.startswith("tests/")
            }
        copied: List[str] = []
        for package_root in sorted(package_roots):
            root_path = os.path.join(source_root, package_root)
            if not os.path.isdir(root_path):
                continue
            for dirpath, _, filenames in os.walk(root_path):
                for filename in filenames:
                    if not filename.endswith(".py"):
                        continue
                    source = os.path.join(dirpath, filename)
                    rel_path = self._normalize_path(os.path.relpath(source, source_root))
                    if rel_path in owned or rel_path.startswith("tests/"):
                        continue
                    target = os.path.join(target_root, rel_path)
                    try:
                        if os.path.exists(target) and self._file_digest(source) == self._file_digest(target):
                            continue
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        shutil.copy2(source, target)
                    except OSError as exc:
                        self.store.append_event(
                            run.id,
                            "dependency_artifact_sync_failed",
                            {
                                "team_id": team.id,
                                "scope_id": spec.scope_id,
                                "artifact": rel_path,
                                "error": str(exc),
                            },
                        )
                        continue
                    copied.append(rel_path)
        if copied:
            self.store.update_team_run_status(
                team.id,
                team.status,
                {"dependency_artifacts_synced": copied[-100:]},
            )
            self.store.append_event(
                run.id,
                "dependency_artifacts_synced",
                {"team_id": team.id, "scope_id": spec.scope_id, "artifacts": copied[-100:]},
            )

    @staticmethod
    def _file_digest(path: str) -> str:
        try:
            import hashlib

            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _system_artifact_paths(source_root: str) -> List[str]:
        roots = [
            ".contractcoding/interfaces",
            ".contractcoding/interface_tests",
            ".contractcoding/scaffolds",
        ]
        paths = [
            ".contractcoding/contract.json",
            ".contractcoding/contract.md",
            ".contractcoding/prd.md",
        ]
        for rel_root in roots:
            abs_root = os.path.join(source_root, rel_root)
            if not os.path.isdir(abs_root):
                continue
            for current, _, filenames in os.walk(abs_root):
                for filename in filenames:
                    rel_path = os.path.relpath(os.path.join(current, filename), source_root).replace("\\", "/")
                    paths.append(rel_path)
        return sorted(dict.fromkeys(paths))

    @staticmethod
    def _uses_isolated_workspace(spec: TeamSpec) -> bool:
        if spec.team_kind == "integration" or spec.promotion_policy.get("mode") == "none":
            return False
        return spec.workspace_plane in {"worktree", "sandbox"}

    @staticmethod
    def _plane_to_record(plane: ExecutionPlane) -> Dict[str, Any]:
        return {
            "mode": plane.mode,
            "module_name": plane.module_name,
            "base_workspace_dir": plane.base_workspace_dir,
            "working_dir": plane.working_dir,
            "root_dir": plane.root_dir,
            "isolated": plane.isolated,
            "repo_root": plane.repo_root,
            "baseline_hashes": dict(plane.baseline_hashes or {}),
            "baseline_dir": plane.baseline_dir,
        }

    @staticmethod
    def _plane_from_record(record: Dict[str, Any]) -> ExecutionPlane:
        return ExecutionPlane(
            mode=str(record.get("mode", "workspace")),
            module_name=str(record.get("module_name", "workspace")),
            base_workspace_dir=str(record.get("base_workspace_dir", "")),
            working_dir=str(record.get("working_dir", "")),
            root_dir=str(record.get("root_dir", record.get("working_dir", ""))),
            isolated=bool(record.get("isolated", False)),
            repo_root=record.get("repo_root") or None,
            baseline_hashes=dict(record.get("baseline_hashes", {}) or {}),
            baseline_dir=record.get("baseline_dir") or None,
        )


class DependencyImpactAnalyzer:
    """Mark dependent teams stale when a stable interface changes."""

    def __init__(self, store: RunStore):
        self.store = store

    def mark_stale(self, run_id: str, changed_scope_id: str, reason: str = "interface changed") -> List[str]:
        contract = self.store.get_contract(run_id)
        if contract is None:
            return []
        scope_by_item = {item.id: item.scope_id for item in contract.work_items}
        affected: Set[str] = set()
        for item in contract.work_items:
            if item.scope_id == changed_scope_id or item.scope_id == "integration":
                continue
            if any(scope_by_item.get(dep) == changed_scope_id for dep in item.depends_on):
                affected.add(item.scope_id)
                continue
            for interface in item.required_interfaces:
                if self._interface_references_scope(interface, changed_scope_id):
                    affected.add(item.scope_id)
                    break

        stale_team_ids: List[str] = []
        for scope_id in sorted(affected):
            team = self.store.get_scope_team_run(run_id, scope_id)
            gate = self.store.get_gate(run_id, f"team:{scope_id}")
            if gate is None or gate.status != "PASSED":
                continue
            self.store.update_gate_status(
                run_id,
                f"team:{scope_id}",
                "PENDING",
                evidence=[f"Gate marked stale after `{changed_scope_id}` promotion: {reason}."],
                metadata={"stale_dependency": changed_scope_id, "stale_reason": reason},
            )
            if team is None:
                continue
            self.store.update_team_run_status(
                team.id,
                "STALE_DEPENDENCY",
                {
                    "stale_dependency": changed_scope_id,
                    "stale_reason": reason,
                    "revalidation_only": team.status in {"PROMOTED", "CLOSED"},
                },
            )
            stale_team_ids.append(team.id)
        if stale_team_ids:
            self.store.append_event(
                run_id,
                "dependent_teams_stale",
                {"changed_scope_id": changed_scope_id, "team_ids": stale_team_ids, "reason": reason},
            )
        return stale_team_ids

    @staticmethod
    def _interface_references_scope(interface: Dict[str, Any], scope_id: str) -> bool:
        for key in ("scope", "scope_id", "from_scope", "depends_on_scope"):
            if str(interface.get(key, "")) == scope_id:
                return True
        source = str(interface.get("from", "")).replace("\\", "/")
        return bool(source and f"/{scope_id}/" in f"/{source}")
