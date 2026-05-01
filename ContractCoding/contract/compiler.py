"""Compile user goals or draft payloads into ContractCoding V8 contracts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from ContractCoding.contract.spec import (
    CONTRACT_VERSION,
    ArchitectureSpec,
    ContractSpec,
    InterfaceSpec,
    FinalGateSpec,
    MilestoneSpec,
    PhaseContract,
    PhaseGateSpec,
    PhaseHandoffSpec,
    RequirementSpec,
    TeamGateSpec,
    WorkScope,
    default_root_scope,
)
from ContractCoding.contract.work_item import WorkItem


CODE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".go", ".rs", ".java", ".cpp", ".c", ".h"}
LARGE_PROJECT_MARKERS = {
    "large",
    "project",
    "package",
    "multi-module",
    "production",
    "大型",
    "项目",
    "包",
}
FUNCTIONAL_SCOPE_ALIASES = {
    "model": "domain",
    "models": "domain",
    "domain": "domain",
    "resource": "domain",
    "resources": "domain",
    "state": "domain",
    "entity": "domain",
    "entities": "domain",
    "schema": "domain",
    "schemas": "domain",
    "citizen": "domain",
    "citizens": "domain",
    "building": "domain",
    "buildings": "domain",
    "tech": "domain",
    "technology": "domain",
    "core": "core",
    "engine": "core",
    "rule": "core",
    "rules": "core",
    "system": "core",
    "systems": "core",
    "economy": "core",
    "turn": "core",
    "turns": "core",
    "scheduler": "core",
    "event": "core",
    "events": "core",
    "construction": "core",
    "exploration": "core",
    "combat": "core",
    "service": "core",
    "services": "core",
    "utils": "core",
    "utility": "core",
    "data": "domain",
    "ai": "ai",
    "agent": "ai",
    "agents": "ai",
    "planner": "planning",
    "planning": "planning",
    "policy": "ai",
    "policies": "ai",
    "opponent": "ai",
    "bot": "ai",
    "io": "io",
    "save": "io",
    "load": "io",
    "save_load": "io",
    "persistence": "io",
    "storage": "io",
    "scenario": "io",
    "scenarios": "io",
    "import": "io",
    "export": "io",
    "loader": "io",
    "serializer": "io",
    "cli": "interface",
    "command": "interface",
    "commands": "interface",
    "main": "interface",
    "api": "interface",
    "adapter": "interface",
    "adapters": "interface",
    "interface": "interface",
    "repl": "interface",
    "server": "interface",
    "terminal": "interface",
    "ui": "interface",
    "frontend": "interface",
    "view": "interface",
    "views": "interface",
}

FUNCTIONAL_SCOPE_ORDER = ["package", "domain", "core", "planning", "ai", "io", "interface", "tests", "app"]


@dataclass(frozen=True)
class PlanningProfile:
    domain: str
    complexity: str
    max_parallel_teams: int
    max_parallel_items_per_team: int
    context_max_chars: int
    recovery_limit: int
    rationale: str

    def to_record(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "complexity": self.complexity,
            "max_parallel_teams": self.max_parallel_teams,
            "max_parallel_items_per_team": self.max_parallel_items_per_team,
            "context_max_chars": self.context_max_chars,
            "recovery_limit": self.recovery_limit,
            "rationale": self.rationale,
        }


class LargeProjectPlanner:
    """Path-aware deterministic planner for larger coding projects.

    The normal coding planner intentionally stays compact. Once a task is large
    enough, though, a single ``game`` or ``app`` scope makes the scheduler look
    serial even when the contract contains many independent files. This planner
    promotes path segments into WorkScopes and adds a system integration gate.
    """

    def __init__(self, compiler: "ContractCompiler"):
        self.compiler = compiler

    def should_plan(self, goal: str, artifacts: List[str]) -> bool:
        lower = goal.lower()
        return len(artifacts) >= 12 or any(marker in lower for marker in LARGE_PROJECT_MARKERS)

    def build(self, goal: str, artifacts: List[str], profile: PlanningProfile) -> ContractSpec:
        scope_by_artifact = self._scope_map_for_artifacts(artifacts)
        scope_order = self._scope_order(scope_by_artifact.values())
        runtime_scope_order = [scope_id for scope_id in scope_order if scope_id != "tests"]
        scopes = [
            WorkScope(
                id=scope_id,
                type=self._scope_type(scope_id),
                label=self._scope_label(scope_id),
                parent_scope="root",
                artifacts=[artifact for artifact, item_scope in scope_by_artifact.items() if item_scope == scope_id],
                conflict_keys=[
                    f"scope:{scope_id}",
                    *[
                        f"artifact:{artifact}"
                        for artifact, item_scope in scope_by_artifact.items()
                        if item_scope == scope_id
                    ],
                ],
                execution_plane_policy="auto",
                team_policy={
                    "team_kind": "coding" if scope_id != "tests" else "tests",
                    "workspace_plane": "worktree" if scope_id != "tests" else "sandbox",
                },
                promotion_policy={"mode": "after_team_gate"},
                interface_stability="stable",
                interfaces=[
                    {
                        "type": "scope_artifacts",
                        "scope": scope_id,
                        "artifacts": [
                            artifact
                            for artifact, item_scope in scope_by_artifact.items()
                            if item_scope == scope_id
                        ],
                    }
                ],
                verification_policy={
                    "layers": ["self_check", "team_gate"],
                    "team_gate_required": True,
                    "team_gate_id": f"team:{scope_id}",
                },
                test_ownership={
                    "owned_tests": [
                        artifact
                        for artifact, item_scope in scope_by_artifact.items()
                        if item_scope == "tests" and self._test_focus_scope(artifact) == scope_id
                    ],
                },
            )
            for scope_id in runtime_scope_order
        ]
        scopes.append(
            WorkScope(
                id="integration",
                type="integration",
                label="System integration gate",
                parent_scope="root",
                artifacts=[".contractcoding/integration_report.json"],
                conflict_keys=["integration:final"],
                execution_plane_policy="auto",
                team_policy={"team_kind": "integration", "workspace_plane": "workspace"},
                promotion_policy={"mode": "none"},
            )
        )

        scaffold_items = self._scaffold_items(goal, scope_by_artifact, scope_order)
        interface_items = self._interface_items(goal, scope_by_artifact, scope_order, scaffold_items)
        coding_items = self._coding_items(goal, artifacts, scope_by_artifact, interface_items)
        work_items = self._with_phase_metadata(scaffold_items, interface_items, coding_items)
        phase_plan = self._phase_plan(goal, runtime_scope_order, work_items)
        team_gates = self._team_gates(goal, artifacts, scope_by_artifact, scope_order)
        final_gate = self._final_gate(goal, artifacts)

        guardrails = {
            "infra_retry_limit": 2,
            "item_repair_limit": max(2, profile.recovery_limit),
            "test_repair_limit": max(4, profile.recovery_limit + 1),
            "contract_replan_limit": max(1, profile.recovery_limit),
        }
        return ContractSpec(
            goals=[goal],
            work_scopes=[default_root_scope(), *scopes],
            work_items=work_items,
            phase_plan=phase_plan,
            team_gates=team_gates,
            final_gate=final_gate,
            acceptance_criteria=[
                "All planned artifacts are present and remain within their WorkScope boundaries.",
                "All generated Python artifacts compile and import successfully.",
                "Package-level tests and final integration checks pass without unexpected writes.",
            ],
            execution_policy={
                "max_parallel_teams": max(4, profile.max_parallel_teams),
                "max_parallel_items_per_team": profile.max_parallel_items_per_team,
                "default_execution_plane": "sandbox",
                "context_max_chars": profile.context_max_chars,
                "autonomy_guardrails": guardrails,
            },
            risk_policy={"ops_default": "approval_required"},
            verification_policy={
                "layers": ["self_check", "team_gate", "final_gate"],
                "self_check": "implicit runtime check for each artifact-level WorkItem",
                "team_gate": "first-class scope gate per functional team",
                "final_gate": "first-class project integration gate",
            },
            test_ownership=self._test_ownership(scope_by_artifact),
            metadata={
                "planner": "large-project-deterministic",
                "task_intent": ContractCompiler._task_intent(goal),
                  "delivery_type": "coding",
                  "architecture": "phase-contract-harness-v8",
                  "planning_pipeline": [
                      "task_classifier",
                      "large_project_scope_planner",
                      "planner_fidelity_guard",
                      "phase_contract_harness",
                      "contract_compiler_validate",
                      "vertical_slice",
                      "team_gates",
                      "final_gate",
                  ],
                "planning_profile": {
                    **profile.to_record(),
                    "autonomy_guardrails": guardrails,
                },
                "large_project": {
                    "artifact_count": len(artifacts),
                    "scope_count": len(runtime_scope_order),
                    "scope_order": runtime_scope_order,
                    "final_test_artifacts": [
                        artifact
                        for artifact, scope_id in scope_by_artifact.items()
                        if scope_id == "tests" and self.compiler._is_test_artifact(artifact)
                    ],
                },
            },
        )

    def _scaffold_items(
        self,
        goal: str,
        scope_by_artifact: Dict[str, str],
        scope_order: List[str],
    ) -> List[WorkItem]:
        items: List[WorkItem] = []
        for scope_id in scope_order:
            if scope_id in {"tests"}:
                continue
            artifacts = [
                artifact
                for artifact, item_scope in scope_by_artifact.items()
                if item_scope == scope_id and artifact.endswith(".py")
            ]
            if not artifacts:
                continue
            preferred = self.compiler._preferred_interface_artifact(scope_id, artifacts)
            if not preferred:
                continue
            items.append(
                WorkItem(
                    id=f"scaffold:{scope_id}",
                    kind="doc",
                    title=f"Scaffold critical public surface for {scope_id}",
                    owner_profile="Architect",
                    module=scope_id,
                    scope_id=scope_id,
                    status="READY",
                    inputs={
                        "goal": goal,
                        "scope_artifacts": artifacts,
                        "preferred_artifact": preferred,
                        "auto_planned": True,
                        "milestone": "critical.scaffold",
                    },
                    target_artifacts=[preferred, f".contractcoding/scaffolds/{scope_id}.json"],
                    acceptance_criteria=[
                        f"Creates a minimal importable scaffold for the critical `{scope_id}` public surface.",
                        "Records symbols and conformance targets; production behavior is completed by team build.",
                    ],
                    conflict_keys=[f"artifact:{preferred}", f"scaffold:{scope_id}"],
                    execution_mode="serial",
                    provided_interfaces=[
                        {
                            "type": "critical_scaffold",
                            "scope": scope_id,
                            "artifact": preferred,
                        }
                    ],
                    dependency_policy="done",
                )
            )
        return items

    def _interface_items(
        self,
        goal: str,
        scope_by_artifact: Dict[str, str],
        scope_order: List[str],
        scaffold_items: Optional[List[WorkItem]] = None,
    ) -> List[WorkItem]:
        scaffold_ids = {item.scope_id: item.id for item in scaffold_items or []}
        items: List[WorkItem] = []
        for scope_id in scope_order:
            if scope_id in {"tests"}:
                continue
            artifacts = [
                artifact
                for artifact, item_scope in scope_by_artifact.items()
                if item_scope == scope_id
            ]
            if not artifacts:
                continue
            items.append(
                WorkItem(
                    id=f"interface:{scope_id}",
                    kind="doc",
                    title=f"Declare public interface for {scope_id}",
                    owner_profile="Architect",
                    module=scope_id,
                    scope_id=scope_id,
                    status="READY",
                    inputs={
                        "goal": goal,
                        "scope_artifacts": artifacts,
                        "auto_planned": True,
                        "milestone": "team.interfaces",
                    },
                    target_artifacts=[
                        f".contractcoding/interfaces/{scope_id}.json",
                        f".contractcoding/interface_tests/{scope_id}.json",
                    ],
                    acceptance_criteria=[
                        f"Declares the public API, data contracts, and cross-scope assumptions for scope `{scope_id}`.",
                        "Lists stable classes, functions, CLI commands, file formats, and known dependencies at a concise level.",
                        "Does not implement production code; it only records the interface contract for worker context.",
                    ],
                    conflict_keys=[f"interface:{scope_id}"],
                    depends_on=[scaffold_ids[scope_id]] if scope_id in scaffold_ids else [],
                    execution_mode="serial",
                    provided_interfaces=[
                        {
                            "type": "scope_interface",
                            "scope": scope_id,
                            "artifacts": artifacts,
                        }
                    ],
                    dependency_policy="done",
                )
            )
        return items

    def _coding_items(
        self,
        goal: str,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
        interface_items: List[WorkItem],
    ) -> List[WorkItem]:
        interface_ids = {item.scope_id: item.id for item in interface_items}
        scope_order = self._scope_order(scope_by_artifact.values())
        implementation_ids: Dict[str, str] = {}
        items: List[WorkItem] = []
        for scope_id in scope_order:
            code_artifacts = self._scope_code_artifacts(artifacts, scope_by_artifact, scope_id)
            if not code_artifacts:
                continue
            dependencies = [
                interface_ids[dep_scope]
                for dep_scope in self._scope_dependency_scope_ids(scope_id, scope_order)
                if dep_scope in interface_ids
            ]
            if scope_id in interface_ids:
                dependencies.insert(0, interface_ids[scope_id])
            dependencies = self._dedupe(dependencies)
            batches = self._implementation_batches(scope_id, code_artifacts)
            first_item_id = ""
            for batch in batches:
                batch_artifacts = list(batch["artifacts"])
                item_id = (
                    f"implement:{scope_id}"
                    if len(batches) == 1
                    else f"implement:{scope_id}:{batch['id']}"
                )
                first_item_id = first_item_id or item_id
                items.append(
                    WorkItem(
                        id=item_id,
                        kind="coding",
                        title=(
                            f"Implement {scope_id} functional scope"
                            if len(batches) == 1
                            else f"Implement {scope_id} batch: {batch['label']}"
                        ),
                        owner_profile=self._owner_for_scope(scope_id, batch_artifacts, goal),
                        module=scope_id,
                        scope_id=scope_id,
                        status="READY",
                        inputs={
                            "goal": goal,
                            "auto_planned": True,
                            "large_project": True,
                            "scope_artifacts": code_artifacts,
                            "batch_artifacts": batch_artifacts,
                            "team_batch": {
                                "id": batch["id"],
                                "label": batch["label"],
                                "scope_id": scope_id,
                                "artifacts": batch_artifacts,
                                "ordinal": batch["ordinal"],
                                "count": len(batches),
                            },
                        },
                        target_artifacts=batch_artifacts,
                        acceptance_criteria=self._large_criteria_for_scope(
                            scope_id,
                            batch_artifacts,
                            goal,
                            batch_id=str(batch["id"]) if len(batches) > 1 else "",
                            scope_artifact_count=len(code_artifacts),
                        ),
                        conflict_keys=[f"artifact:{artifact}" for artifact in batch_artifacts],
                        depends_on=dependencies,
                        serial_group="interface" if scope_id == "interface" else "",
                        execution_mode=(
                            "serial"
                            if scope_id == "interface"
                            or any(self._is_save_load_artifact(artifact) for artifact in batch_artifacts)
                            else "auto"
                        ),
                        team_role_hint="implementation_worker",
                        provided_interfaces=self._provided_interfaces_for_scope(scope_id, batch_artifacts, goal),
                        required_interfaces=self._required_interfaces_for_scope(scope_id, scope_order),
                        dependency_policy="done",
                        context_policy={
                            "batch_id": batch["id"],
                            "batch_scope": scope_id,
                            "scope_artifact_count": len(code_artifacts),
                        },
                    )
                )
            implementation_ids[scope_id] = first_item_id
        return items

    def _with_phase_metadata(
        self,
        scaffold_items: List[WorkItem],
        interface_items: List[WorkItem],
        coding_items: List[WorkItem],
    ) -> List[WorkItem]:
        items: List[WorkItem] = []
        for item in [*scaffold_items, *interface_items]:
            items.append(self._item_with_phase(item, "vertical_slice"))
        for item in coding_items:
            batch = dict(item.inputs.get("team_batch", {}) or {})
            phase_id = self._phase_id_for_batch(item.scope_id, str(batch.get("id", "")))
            items.append(self._item_with_phase(item, phase_id))
        return items

    @staticmethod
    def _item_with_phase(item: WorkItem, phase_id: str) -> WorkItem:
        payload = item.to_record()
        payload["inputs"] = {**dict(payload.get("inputs", {})), "phase_id": phase_id}
        return WorkItem.from_mapping(payload)

    @staticmethod
    def _phase_id_for_batch(scope_id: str, batch_id: str) -> str:
        if scope_id == "package":
            return "vertical_slice"
        vertical_batches = {
            "domain": {"resources", "entities", "domain"},
            "core": {"engine"},
            "io": {"persistence", "scenarios"},
        }
        if batch_id in vertical_batches.get(scope_id, set()):
            return "vertical_slice"
        if scope_id in {"domain", "core"}:
            return "feature:simulation"
        if scope_id in {"planning", "ai", "io"}:
            return "feature:planning_io"
        if scope_id == "interface":
            return "feature:interface"
        if scope_id in {"tests", "app"}:
            return f"feature:{scope_id}"
        return "feature:core"

    def _phase_plan(
        self,
        goal: str,
        runtime_scope_order: List[str],
        items: List[WorkItem],
    ) -> List[PhaseContract]:
        items_by_phase: Dict[str, List[WorkItem]] = {}
        for item in items:
            phase_id = str(item.inputs.get("phase_id", "") or "feature:core")
            items_by_phase.setdefault(phase_id, []).append(item)

        phases: List[PhaseContract] = [
            PhaseContract(
                phase_id="requirements.freeze",
                goal="Freeze the PRD Lite: user goal, constraints, quality bar, and acceptance scenarios.",
                mode="serial",
                entry_conditions=[],
                teams_in_scope=[],
                deliverables=[".contractcoding/prd.md"],
                phase_gate=PhaseGateSpec(
                    checks=["prd_projection"],
                    criteria=["Requirements are explicit enough to create testable phase contracts."],
                ),
                handoff=PhaseHandoffSpec(
                    artifacts=[".contractcoding/prd.md"],
                    notes=["Implementation work is not activated by this phase."],
                ),
            ),
            PhaseContract(
                phase_id="architecture.sketch",
                goal="Sketch functional bounded contexts, coarse artifacts, and dependency direction.",
                mode="serial",
                entry_conditions=["requirements.freeze passed"],
                teams_in_scope=[scope for scope in runtime_scope_order if scope != "tests"],
                deliverables=[".contractcoding/contract.md"],
                phase_gate=PhaseGateSpec(
                    checks=["bounded_contexts", "artifact_ownership"],
                    criteria=["Teams are functional domains; files and methods remain team-internal batches."],
                ),
            ),
        ]

        vertical_items = items_by_phase.get("vertical_slice", [])
        if vertical_items:
            phases.append(
                PhaseContract(
                    phase_id="vertical_slice",
                    goal=(
                        "Build the smallest real end-to-end path across domain, core, IO, "
                        "and interface before expanding feature breadth."
                    ),
                    mode="hybrid",
                    entry_conditions=["architecture.sketch passed"],
                    teams_in_scope=self._teams_for_phase(vertical_items),
                    deliverables=self._deliverables_for_phase(vertical_items),
                    phase_gate=PhaseGateSpec(
                        checks=["system_artifact_sync", "compile_import", "vertical_cli_or_api_smoke"],
                        criteria=[
                            "Critical scaffolds and interface artifacts exist.",
                            "The minimal scenario can compile/import through public APIs.",
                        ],
                    ),
                    handoff=PhaseHandoffSpec(
                        artifacts=self._system_contract_artifacts(),
                        notes=["Feature phases may rely on these frozen contract artifacts."],
                    ),
                )
            )

        feature_order = [
            ("feature:simulation", "Expand simulation depth across domain and core behavior."),
            ("feature:planning_io", "Expand planning, persistence, scenarios, and map IO."),
            ("feature:interface", "Expand public CLI/REPL/API interactions over stable runtime behavior."),
            ("feature:tests", "Add integration-focused test scaffolding and regression coverage."),
            ("feature:app", "Expand app-level glue and adapters."),
        ]
        emitted_feature_phases = {"vertical_slice"}
        emitted_prior_features: List[str] = []
        for phase_id, goal_text in feature_order:
            phase_items = items_by_phase.get(phase_id, [])
            if not phase_items:
                continue
            emitted_feature_phases.add(phase_id)
            entry_conditions = self._feature_phase_entry_conditions(phase_id, emitted_prior_features)
            phases.append(
                PhaseContract(
                    phase_id=phase_id,
                    goal=goal_text,
                    mode="parallel",
                    entry_conditions=entry_conditions,
                    teams_in_scope=self._teams_for_phase(phase_items),
                    deliverables=[],
                    phase_gate=PhaseGateSpec(
                        checks=["self_check", "team_gate"],
                        criteria=[
                            "Phase implementation batches self-check.",
                            "Affected team gates can validate public behavior.",
                        ],
                    ),
                )
            )
            emitted_prior_features.append(phase_id)
        for phase_id in sorted(items_by_phase):
            if phase_id in emitted_feature_phases:
                continue
            phase_items = items_by_phase.get(phase_id, [])
            if not phase_items:
                continue
            phases.append(
                PhaseContract(
                    phase_id=phase_id,
                    goal=f"Execute phase `{phase_id}` after the vertical slice is stable.",
                    mode="parallel",
                    entry_conditions=["vertical_slice passed"],
                    teams_in_scope=self._teams_for_phase(phase_items),
                    deliverables=[],
                    phase_gate=PhaseGateSpec(
                        checks=["self_check", "team_gate"],
                        criteria=["Phase implementation batches self-check."],
                    ),
                )
            )

        phases.append(
            PhaseContract(
                phase_id="hardening",
                goal="Run focused team gates, repair integration drift, and prepare promotion handoff.",
                mode="hybrid",
                entry_conditions=["all feature phases passed"],
                teams_in_scope=[scope for scope in runtime_scope_order if scope != "tests"],
                deliverables=["team gate evidence", "promoted team artifacts"],
                phase_gate=PhaseGateSpec(
                    checks=["team_gates", "promotion_readiness"],
                    criteria=["Each functional team has passed its gate before final integration."],
                ),
            )
        )
        phases.append(
            PhaseContract(
                phase_id="final_acceptance",
                goal="Verify the promoted workspace with deterministic system checks and final evaluator review.",
                mode="serial",
                entry_conditions=["hardening passed"],
                teams_in_scope=["integration"],
                deliverables=[".contractcoding/integration_report.json"],
                phase_gate=PhaseGateSpec(
                    checks=["compileall", "import_all", "unittest_discover", "cli_smoke", "scenario"],
                    criteria=["Final deterministic gate passes; LLM review cannot override deterministic failure."],
                ),
            )
        )
        return phases

    @staticmethod
    def _feature_phase_entry_conditions(phase_id: str, prior_feature_phases: List[str]) -> List[str]:
        if phase_id == "feature:interface" and prior_feature_phases:
            return [f"{prior} passed" for prior in prior_feature_phases]
        if phase_id == "feature:planning_io" and "feature:simulation" in prior_feature_phases:
            return ["feature:simulation passed"]
        return ["vertical_slice passed"]

    @staticmethod
    def _teams_for_phase(items: List[WorkItem]) -> List[str]:
        return ContractCompiler._dedupe([item.scope_id for item in items if item.scope_id])

    @staticmethod
    def _deliverables_for_phase(items: List[WorkItem]) -> List[str]:
        return ContractCompiler._dedupe(
            artifact
            for item in items
            for artifact in item.target_artifacts
            if artifact
        )[:12]

    @staticmethod
    def _system_contract_artifacts() -> List[str]:
        return [
            ".contractcoding/contract.json",
            ".contractcoding/contract.md",
            ".contractcoding/prd.md",
            ".contractcoding/interfaces/*.json",
            ".contractcoding/interface_tests/*.json",
            ".contractcoding/scaffolds/*.json",
        ]

    def _implementation_batches(self, scope_id: str, artifacts: List[str]) -> List[Dict[str, Any]]:
        """Split a large functional team into execution batches.

        A batch is still owned by the same TeamRun. This avoids file-level teams
        while keeping LLM prompts small enough to finish and repair reliably.
        No LOC target is assigned here; batch quality is defined by artifacts,
        interfaces, and acceptance criteria.
        """

        force_semantic_split = scope_id in {"domain", "core", "io", "interface"}
        if len(artifacts) <= 4 and not force_semantic_split:
            return [
                {
                    "id": scope_id,
                    "label": self._scope_label(scope_id),
                    "artifacts": list(artifacts),
                    "ordinal": 1,
                }
            ]

        grouped: Dict[str, List[str]] = {}
        for artifact in artifacts:
            key = self._batch_key_for_artifact(scope_id, artifact)
            grouped.setdefault(key, []).append(artifact)

        if force_semantic_split and len(grouped) <= 1:
            return [
                {
                    "id": self._safe_batch_id(os.path.splitext(os.path.basename(artifact))[0]),
                    "label": self._batch_label(scope_id, os.path.splitext(os.path.basename(artifact))[0]),
                    "artifacts": [artifact],
                    "ordinal": index,
                }
                for index, artifact in enumerate(artifacts, start=1)
            ]

        raw_batches: List[Dict[str, Any]] = []
        for key, values in grouped.items():
            for index, chunk in enumerate(self._chunks(values, 4), start=1):
                batch_id = key if len(values) <= 4 else f"{key}_{index}"
                raw_batches.append(
                    {
                        "id": batch_id,
                        "label": self._batch_label(scope_id, batch_id),
                        "artifacts": chunk,
                    }
                )

        if len(raw_batches) == 1:
            raw_batches = [
                {
                    "id": f"{scope_id}_{index}",
                    "label": f"{self._scope_label(scope_id)} batch {index}",
                    "artifacts": chunk,
                }
                for index, chunk in enumerate(self._chunks(artifacts, 4), start=1)
            ]

        seen: Dict[str, int] = {}
        batches: List[Dict[str, Any]] = []
        for ordinal, batch in enumerate(raw_batches, start=1):
            base_id = self._safe_batch_id(str(batch["id"]))
            count = seen.get(base_id, 0) + 1
            seen[base_id] = count
            batch_id = base_id if count == 1 else f"{base_id}_{count}"
            batches.append(
                {
                    "id": batch_id,
                    "label": str(batch["label"]),
                    "artifacts": list(batch["artifacts"]),
                    "ordinal": ordinal,
                }
            )
        return batches

    def _batch_key_for_artifact(self, scope_id: str, artifact: str) -> str:
        normalized = self._normalize_artifact_path(artifact)
        stem = os.path.splitext(os.path.basename(normalized))[0].lower()
        segments = [segment.lower() for segment in normalized.split("/")]
        if scope_id == "core":
            if "systems" in segments:
                return "systems"
            if stem in {"state", "rules", "engine", "scheduler", "simulation", "events"}:
                return "engine"
            if stem in {"economy", "construction", "research"}:
                return "development"
            if stem in {"logistics", "diplomacy", "disasters", "victory"}:
                return "systems"
            return "core"
        if scope_id == "domain":
            if stem in {"resources", "resource"}:
                return "resources"
            if stem in {"citizens", "citizen", "entities", "entity"}:
                return "entities"
            if stem in {"buildings", "building", "technology", "tech"}:
                return "structures"
            return "domain"
        if scope_id in {"ai", "planning"}:
            if stem in {"planner", "planning"}:
                return "planning"
            if stem in {"policy", "policies", "governor", "opponent"}:
                return "policy"
            if stem in {"heuristic", "heuristics"}:
                return "heuristics"
            return "ai"
        if scope_id == "io":
            if any(token in stem for token in ("save", "load", "storage", "persistence")):
                return "persistence"
            if "scenario" in stem:
                return "scenarios"
            if any(token in stem for token in ("map", "route", "terrain")):
                return "maps"
            return "io"
        if scope_id == "interface":
            if stem in {"cli", "main", "__main__"}:
                return "cli"
            if stem in {"repl", "rendering", "ui", "view"}:
                return "interaction"
            return "interface"
        if scope_id == "package":
            return "package"
        return scope_id

    @staticmethod
    def _safe_batch_id(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
        return cleaned or "batch"

    @staticmethod
    def _chunks(values: List[str], size: int) -> List[List[str]]:
        return [values[index : index + size] for index in range(0, len(values), size)]

    @staticmethod
    def _batch_label(scope_id: str, batch_id: str) -> str:
        labels = {
            "engine": "engine, rules, scheduling, and state flow",
            "systems": "simulation systems and domain interactions",
            "development": "economy, construction, and research systems",
            "resources": "resource model and accounting",
            "entities": "entities, citizens, and state records",
            "structures": "buildings, technology, and structures",
            "planning": "planning and recommendations",
            "policy": "policy, governor, and prioritization",
            "heuristics": "heuristics, ranking, and scoring",
            "persistence": "save/load and persistence",
            "scenarios": "scenario loading and fixtures",
            "maps": "maps, geography, and route helpers",
            "cli": "CLI entrypoints and commands",
            "interaction": "interactive rendering and REPL",
            "package": "package metadata and exports",
        }
        return labels.get(batch_id, f"{scope_id} {batch_id} batch")

    def _team_gates(
        self,
        goal: str,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
        scope_order: List[str],
    ) -> List[TeamGateSpec]:
        gates: List[TeamGateSpec] = []
        for scope_id in scope_order:
            if scope_id in {"integration", "tests"}:
                continue
            scope_artifacts = [
                artifact
                for artifact in artifacts
                if scope_by_artifact.get(artifact) == scope_id and not self.compiler._is_test_artifact(artifact)
            ]
            test_artifacts = self._scope_test_artifacts(artifacts, scope_by_artifact, scope_id)
            if not scope_artifacts and not test_artifacts:
                continue
            gates.append(
                TeamGateSpec(
                    scope_id=scope_id,
                    test_artifacts=test_artifacts,
                    test_plan=self._test_plan_for_scope(scope_id, scope_artifacts, test_artifacts, goal, scope_order),
                    deterministic_checks=self._team_gate_checks(goal),
                )
            )
        return gates

    def _final_gate(self, goal: str, artifacts: List[str]) -> FinalGateSpec:
        python_artifacts = [artifact for artifact in artifacts if artifact.endswith(".py")]
        return FinalGateSpec(
            required_artifacts=artifacts,
            python_artifacts=python_artifacts,
            package_roots=self._package_roots(python_artifacts),
            requires_tests=any(self.compiler._is_test_artifact(artifact) for artifact in artifacts),
            final_acceptance_scenarios=self._final_acceptance_scenarios(goal),
            product_behavior=self._product_behavior_contract(goal, artifacts),
        )

    def _test_plan_for_scope(
        self,
        scope_id: str,
        scope_artifacts: List[str],
        test_artifacts: List[str],
        goal: str,
        scope_order: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        scope_order = list(scope_order or [scope_id])
        return {
            "required_public_interfaces": scope_artifacts,
            "test_artifacts": test_artifacts,
            "required_behaviors": self._required_behaviors_for_scope(scope_id, goal),
            "test_strata": "scope_local",
            "gate_depth": "smoke",
            "dependency_scope_ids": self._scope_dependency_scope_ids(scope_id, scope_order),
        }

    @staticmethod
    def _team_gate_checks(goal: str = "") -> List[str]:
        mode = os.getenv("CONTRACTCODING_TEAM_GATE_MODE", "smoke").strip().lower()
        if mode in {"strict", "scope_tests", "tests"}:
            return ["artifact_coverage", "syntax_import", "scope_tests", "placeholder_scan"]
        return ["artifact_coverage", "syntax_import", "interface_conformance", "functional_smoke", "placeholder_scan"]

    @staticmethod
    def _required_behaviors_for_scope(scope_id: str, goal: str) -> List[str]:
        behaviors_by_scope = {
            "domain": ["state roundtrip", "resource accounting", "entity invariants"],
            "core": ["deterministic turns", "rule constraints", "system interactions"],
            "planning": ["deterministic recommendations", "policy ordering", "edge cases"],
            "ai": ["deterministic recommendations", "policy ordering", "edge cases"],
            "io": ["serialization roundtrip", "scenario loading", "report generation"],
            "interface": ["CLI smoke", "argument validation", "human-readable rendering"],
            "package": ["package imports", "version/export surface"],
            "tests": ["integration coverage", "non-empty executable assertions"],
        }
        return behaviors_by_scope.get(scope_id, ["scope acceptance criteria", "public API behavior"])

    @staticmethod
    def _final_acceptance_scenarios(goal: str) -> List[Dict[str, Any]]:
        lower = str(goal or "").lower()
        scenarios: List[Dict[str, Any]] = [
            {
                "id": "package_integrity",
                "description": "Required artifacts compile, import, and expose stable public APIs.",
            }
        ]
        if any(token in lower for token in ("cli", "command", "repl", "entrypoint")):
            scenarios.append(
                {
                    "id": "cli_smoke",
                    "description": "Public CLI/API entrypoints run against generated scenarios.",
                }
            )
        if any(token in lower for token in ("save", "load", "persistent", "roundtrip", "scenario")):
            scenarios.append(
                {
                    "id": "save_load_roundtrip",
                    "description": "A realistic scenario can be serialized, loaded, resumed, and inspected with stable data contracts.",
                }
            )
        if any(token in lower for token in ("engine", "simulation", "turn", "30-turn", "30 turn", "thirty")):
            scenarios.append(
                {
                    "id": "multi_turn_simulation",
                    "description": "The simulation reaches the requested turn count deterministically and preserves core state.",
                }
            )
        if any(token in lower for token in ("domain", "colony", "population", "resource")):
            scenarios.append(
                {
                    "id": "domain_to_engine_state",
                    "description": "Domain entities and operational state feed the engine without lossy conversion.",
                }
            )
        if any(token in lower for token in ("ai", "planning", "planner", "policy", "heuristic")):
            scenarios.append(
                {
                    "id": "planning_policy",
                    "description": "Planner, policy, and heuristic functions operate on real engine/domain state deterministically.",
                }
            )
        return LargeProjectPlanner._dedupe_scenarios(scenarios)

    @staticmethod
    def _product_behavior_contract(goal: str, artifacts: List[str]) -> Dict[str, Any]:
        """Derive product-level behavior checks from the requested product shape.

        This is intentionally conservative: it only adds checks for capabilities
        strongly implied by the user goal or path names, and the checks are
        deterministic enough to run in the final gate.
        """

        text = " ".join([goal, *artifacts]).lower()
        capabilities: List[str] = ["package_integrity"]

        def has_any(*tokens: str) -> bool:
            return any(token in text for token in tokens)

        if has_any("cli", "command", "entrypoint", "terminal", "interface/cli.py", "/cli.py", "cli/main.py"):
            capabilities.append("cli_blackbox")
        if has_any("repl", "interface/repl.py"):
            capabilities.append("repl")
        if has_any("simulation", "simulate", "engine", "turn", "tick", "scenario"):
            capabilities.append("simulation")
        if has_any("event-sourced", "event sourced", "event sourcing", "event_store", "event store", "replay"):
            capabilities.append("event_sourcing")
        if has_any("save", "load", "persist", "persistence", "roundtrip", "round-trip"):
            capabilities.append("persistence")
        if has_any("scenario", "scenarios"):
            capabilities.append("scenario_loading")
        if has_any("plan", "planner", "planning", "policy", "optimizer", "heuristic", "ai"):
            capabilities.append("planning")
        if has_any("route", "routing", "map", "maps", "waypoint"):
            capabilities.append("routing")
        if has_any("resource", "accounting", "ledger", "fuel", "cargo"):
            capabilities.append("resource_accounting")
        if has_any("maintenance"):
            capabilities.append("maintenance")
        if has_any("failure", "failures", "repair", "disaster"):
            capabilities.append("failure_repair")
        if has_any("report", "summary", "telemetry"):
            capabilities.append("reporting")

        capabilities = LargeProjectPlanner._dedupe(capabilities)
        blackbox_commands = LargeProjectPlanner._blackbox_commands_for_artifacts(artifacts)
        semantic_requirements = LargeProjectPlanner._semantic_requirements_for_capabilities(capabilities, artifacts)
        if not blackbox_commands and not semantic_requirements and capabilities == ["package_integrity"]:
            return {}
        return {
            "capabilities": capabilities,
            "blackbox_commands": blackbox_commands,
            "semantic_requirements": semantic_requirements,
        }

    @staticmethod
    def _blackbox_commands_for_artifacts(artifacts: List[str]) -> List[Dict[str, Any]]:
        commands: List[Dict[str, Any]] = []
        for module in LargeProjectPlanner._cli_modules_for_artifacts(artifacts):
            commands.append(
                {
                    "id": f"{module}:help",
                    "argv": ["{python}", "-m", module, "--help"],
                    "expected_returncode": 0,
                    "stdout_contains_any": ["usage", "help", "commands"],
                    "require_output": True,
                    "timeout_seconds": 10,
                }
            )
        return commands

    @staticmethod
    def _cli_modules_for_artifacts(artifacts: List[str]) -> List[str]:
        modules: List[str] = []
        for artifact in artifacts:
            normalized = str(artifact).replace("\\", "/").strip("/")
            if not normalized.endswith(".py"):
                continue
            stem = normalized[:-3]
            parts = [part for part in stem.split("/") if part]
            if not parts or not all(part.isidentifier() for part in parts):
                continue
            name = parts[-1]
            if name not in {"cli", "main", "__main__"} and "interface" not in parts:
                continue
            if name == "__main__":
                parts = parts[:-1]
            if not parts:
                continue
            module = ".".join(parts)
            if module and module not in modules:
                modules.append(module)
        return modules[:3]

    @staticmethod
    def _semantic_requirements_for_capabilities(capabilities: List[str], artifacts: List[str]) -> List[Dict[str, Any]]:
        requirements: List[Dict[str, Any]] = []
        tests = [artifact for artifact in artifacts if LargeProjectPlanner._is_test_path(artifact)]
        implementation = [artifact for artifact in artifacts if artifact.endswith(".py") and not LargeProjectPlanner._is_test_path(artifact)]
        core_consumers = [
            artifact
            for artifact in implementation
            if any(part in artifact.lower() for part in ("core/", "engine", "simulation"))
        ] or implementation
        cli_consumers = [
            artifact
            for artifact in implementation
            if any(part in artifact.lower() for part in ("interface/cli.py", "/cli.py", "cli/main.py"))
        ]

        if "event_sourcing" in capabilities:
            requirements.append(
                {
                    "id": "event_sourcing_replay_is_exercised",
                    "description": "Event sourcing must include replay/event-log behavior in final tests and implementation.",
                    "required_terms": ["event_log", "replay"],
                    "test_artifacts": tests,
                    "implementation_artifacts": implementation,
                    "must_appear_in_tests": bool(tests),
                    "must_appear_in_implementation": bool(implementation),
                }
            )
        if "failure_repair" in capabilities:
            requirements.append(
                {
                    "id": "failure_repair_is_semantic_not_note_text",
                    "description": "Failure/repair must produce real failure and repair semantics, not only narrative notes.",
                    "required_terms": ["failure_recorded", "failure_repaired"],
                    "test_artifacts": tests,
                    "implementation_artifacts": implementation,
                    "consumer_artifacts": core_consumers,
                    "consumer_terms": ["FailureEngine", "failure_recorded", "failure_repaired"],
                    "must_appear_in_tests": bool(tests),
                    "must_appear_in_implementation": bool(implementation),
                }
            )
        if "maintenance" in capabilities:
            requirements.append(
                {
                    "id": "maintenance_is_exercised",
                    "description": "Maintenance must be represented by executable behavior and tests.",
                    "required_any_terms": ["MaintenancePlanner", "maintenance_performed", "maintenance_due"],
                    "test_artifacts": tests,
                    "implementation_artifacts": implementation,
                    "must_appear_in_tests": bool(tests),
                    "must_appear_in_implementation": bool(implementation),
                }
            )
        if "planning" in capabilities and "routing" in capabilities:
            requirements.append(
                {
                    "id": "routing_feeds_planning",
                    "description": "Route data must feed planning/optimization rather than living as an unused side module.",
                    "required_any_terms": ["RouteNetwork", "route_network", "shortest_path", "load_map"],
                    "test_artifacts": tests,
                    "implementation_artifacts": implementation,
                    "consumer_artifacts": cli_consumers or core_consumers,
                    "consumer_terms": ["RouteNetwork", "route_network", "load_map", "shortest_path"],
                    "must_appear_in_tests": bool(tests),
                    "must_appear_in_implementation": bool(implementation),
                }
            )
        if "reporting" in capabilities:
            requirements.append(
                {
                    "id": "reports_are_asserted_not_only_rendered",
                    "description": "Reports must be tested for structured/human-readable consistency.",
                    "required_any_terms": ["render_simulation_report", "render_state_report", "summary", "report_text"],
                    "test_artifacts": tests,
                    "implementation_artifacts": implementation,
                    "must_appear_in_tests": bool(tests),
                    "must_appear_in_implementation": bool(implementation),
                }
            )
        return requirements

    @staticmethod
    def _is_test_path(path: str) -> bool:
        normalized = str(path).replace("\\", "/")
        name = os.path.basename(normalized)
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    @staticmethod
    def _dedupe_scenarios(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for scenario in scenarios:
            scenario_id = str(scenario.get("id", "")).strip()
            if not scenario_id or scenario_id in seen:
                continue
            seen.add(scenario_id)
            out.append(scenario)
        return out

    def _large_criteria_for_scope(
        self,
        scope_id: str,
        artifacts: List[str],
        goal: str,
        *,
        batch_id: str = "",
        scope_artifact_count: int = 0,
    ) -> List[str]:
        readable_artifacts = ", ".join(artifacts[:8]) + ("..." if len(artifacts) > 8 else "")
        scope_phrase = f"`{scope_id}` functional scope"
        if batch_id:
            scope_phrase = f"`{scope_id}` functional scope batch `{batch_id}`"
        criteria = [
            f"Implements the {scope_phrase} across all target artifacts: {readable_artifacts}.",
            "Writes only the target artifacts assigned to this team WorkItem.",
            "Keeps public imports stable according to the generated scope interface artifact.",
            "Python modules are importable with no interactive prompts or long-running work at import time.",
            "No TODO, pass-only, NotImplementedError, or placeholder behavior remains.",
            "Prioritizes correct behavior and clear public interfaces over padding code volume.",
        ]
        return criteria

    def _large_test_criteria_for_scope(
        self,
        scope_id: str,
        test_artifacts: List[str],
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
    ) -> List[str]:
        code_artifacts = [
            artifact
            for artifact in artifacts
            if scope_by_artifact.get(artifact) == scope_id and not self.compiler._is_test_artifact(artifact)
        ]
        if scope_id == "tests":
            code_artifacts = [
                artifact
                for artifact in artifacts
                if not self.compiler._is_test_artifact(artifact)
            ]
        return [
            f"Creates focused unittest coverage for `{scope_id}` behavior in: {', '.join(test_artifacts)}.",
            f"Tests import the real generated modules for this scope: {', '.join(code_artifacts[:8]) or 'integration package modules'}.",
            "Tests include executable assertions and must not skip solely because a guessed public API is unavailable.",
            "Tests map directly to scope acceptance criteria and public interfaces, not private implementation guesses.",
            "Tests are runnable by direct unittest execution and package-level unittest discovery.",
        ]

    def _owner_for_scope(self, scope_id: str, artifacts: List[str], goal: str) -> str:
        if scope_id in {"planning", "ai"}:
            return "Algorithm_Engineer"
        if scope_id == "interface" or any(artifact.endswith((".tsx", ".jsx", ".css", ".html")) for artifact in artifacts):
            return "Frontend_Engineer" if any(artifact.endswith((".tsx", ".jsx", ".css", ".html")) for artifact in artifacts) else "Backend_Engineer"
        return "Backend_Engineer"

    def _provided_interfaces_for_scope(
        self,
        scope_id: str,
        artifacts: List[str],
        goal: str,
    ) -> List[Dict[str, Any]]:
        interfaces: List[Dict[str, Any]] = [
            {"type": "scope_implementation", "scope": scope_id, "artifacts": list(artifacts)}
        ]
        for artifact in artifacts:
            for interface in self.compiler._provided_interfaces_for_artifact(artifact, goal):
                record = dict(interface)
                record.setdefault("artifact", artifact)
                interfaces.append(record)
        return interfaces

    def _required_interfaces_for_scope(self, scope_id: str, scope_order: List[str]) -> List[Dict[str, Any]]:
        return [
            {
                "from_scope": dep_scope,
                "interface_artifact": f".contractcoding/interfaces/{dep_scope}.json",
                "imports": ["stable public scope APIs"],
            }
            for dep_scope in self._scope_dependency_scope_ids(scope_id, scope_order)
        ]

    def _required_interfaces_for_test_scope(
        self,
        scope_id: str,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        selected = [
            artifact
            for artifact in artifacts
            if not self.compiler._is_test_artifact(artifact) and scope_by_artifact.get(artifact) == scope_id
        ]
        if scope_id == "tests" or not selected:
            selected = [
                artifact
                for artifact in artifacts
                if not self.compiler._is_test_artifact(artifact)
            ]
        return [
            {"from": artifact, "imports": ["public module APIs"]}
            for artifact in selected[:12]
        ]

    def _scope_dependency_scope_ids(self, scope_id: str, scope_order: List[str]) -> List[str]:
        available = set(scope_order)
        dependencies_by_scope = {
            "package": [],
            "domain": ["package"],
            "core": ["package", "domain"],
            "planning": ["package", "domain", "core"],
            "ai": ["package", "domain", "core", "planning"],
            "io": ["package", "domain", "core"],
            "interface": ["package", "domain", "core", "planning", "ai", "io"],
            "tests": ["package", "domain", "core", "planning", "ai", "io", "interface"],
            "app": ["package", "domain", "core"],
        }
        return [scope for scope in dependencies_by_scope.get(scope_id, []) if scope in available]

    def _test_dependency_scopes(self, scope_id: str, scope_order: List[str]) -> List[str]:
        if scope_id == "tests":
            return [
                scope
                for scope in scope_order
                if scope not in {"tests"} and scope != "integration"
            ]
        return [scope_id]

    def _scope_map_for_artifacts(self, artifacts: List[str]) -> Dict[str, str]:
        scope_by_artifact: Dict[str, str] = {}
        for artifact in artifacts:
            normalized = self._normalize_artifact_path(artifact)
            if self.compiler._is_test_artifact(normalized):
                continue
            scope_by_artifact[artifact] = self._scope_id_for_artifact(normalized)

        available_scopes = set(scope_by_artifact.values())
        for artifact in artifacts:
            normalized = self._normalize_artifact_path(artifact)
            if not self.compiler._is_test_artifact(normalized):
                continue
            focus_scope = self._scope_for_test_artifact(normalized)
            if focus_scope in available_scopes:
                scope_by_artifact[artifact] = focus_scope
            elif focus_scope in {"integration", "regression"}:
                scope_by_artifact[artifact] = "tests"
            else:
                scope_by_artifact[artifact] = focus_scope if focus_scope in FUNCTIONAL_SCOPE_ORDER else "tests"

        return self._collapse_scope_map(scope_by_artifact)

    def _collapse_scope_map(self, scope_by_artifact: Dict[str, str]) -> Dict[str, str]:
        scopes = set(scope_by_artifact.values())
        if len(scopes) <= 8:
            return scope_by_artifact

        priority = {scope: index for index, scope in enumerate(FUNCTIONAL_SCOPE_ORDER)}
        counts: Dict[str, int] = {}
        for scope_id in scopes:
            counts[scope_id] = sum(1 for value in scope_by_artifact.values() if value == scope_id)

        merged = dict(scope_by_artifact)
        while len(set(merged.values())) > 8:
            protected = {"tests", "interface", "package", "domain", "core", "ai", "io"}
            candidates = [
                scope
                for scope in set(merged.values())
                if scope not in protected
            ]
            if not candidates:
                break
            tiny = min(candidates, key=lambda scope: (counts.get(scope, 0), -priority.get(scope, 99), scope))
            target = self._merge_target_for_scope(tiny, set(merged.values()))
            for artifact, scope in list(merged.items()):
                if scope == tiny:
                    merged[artifact] = target
        return merged

    @staticmethod
    def _merge_target_for_scope(scope_id: str, available: set[str]) -> str:
        protected = {"package", "domain", "core", "ai", "io", "interface", "tests"}
        if scope_id in protected:
            return scope_id
        return "core" if "core" in available else sorted(available)[0]

    def _scope_code_artifacts(
        self,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
        scope_id: str,
    ) -> List[str]:
        return [
            artifact
            for artifact in artifacts
            if scope_by_artifact.get(artifact) == scope_id and not self.compiler._is_test_artifact(artifact)
        ]

    def _scope_test_artifacts(
        self,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
        scope_id: str,
    ) -> List[str]:
        return [
            artifact
            for artifact in artifacts
            if scope_by_artifact.get(artifact) == scope_id and self.compiler._is_test_artifact(artifact)
        ]

    def _large_criteria_for_artifact(self, artifact: str, goal: str, *, is_test: bool) -> List[str]:
        if is_test:
            return [
                f"Contains focused unittest tests for the actual generated package modules relevant to `{artifact}`.",
                "Imports contract target modules from the generated package, not guessed tutorial/sample APIs unless those files are explicit targets.",
                "Tests include executable assertions and must not skip solely because a guessed public API is unavailable.",
                "Tests must exercise public APIs rather than private implementation details where possible.",
                "Tests must be runnable by package-level unittest discovery.",
            ]
        base = self.compiler._criteria_for_artifact(artifact, goal)
        return [
            *base,
            "The file stays inside its declared WorkScope and does not mutate unrelated artifacts.",
            "The module is importable with no interactive prompts or long-running work at import time.",
            "No TODO, pass-only, NotImplementedError, or placeholder behavior remains.",
        ]

    def _large_required_interfaces_for_artifact(
        self,
        artifact: str,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
        *,
        is_test: bool,
    ) -> List[Dict[str, Any]]:
        if not is_test:
            return self.compiler._required_interfaces_for_artifact(artifact, "")

        focus = self._test_focus_scope(artifact)
        code_artifacts = [
            candidate
            for candidate in artifacts
            if not self.compiler._is_test_artifact(candidate)
        ]
        if focus in {"integration", "regression"}:
            selected = code_artifacts
        elif focus:
            selected = [
                candidate
                for candidate in code_artifacts
                if scope_by_artifact.get(candidate) == focus
            ]
        else:
            selected = []
        if not selected:
            selected = code_artifacts
        return [
            {
                "from": candidate,
                "imports": ["public module APIs"],
            }
            for candidate in selected[:8]
        ]

    @staticmethod
    def _test_focus_scope(artifact: str) -> str:
        name = os.path.basename(artifact).lower()
        if name.startswith("test_"):
            name = name[len("test_"):]
        if name.endswith("_test.py"):
            name = name[: -len("_test.py")]
        elif name.endswith(".py"):
            name = name[:-3]
        return name.replace("-", "_")

    def _test_dependency_artifacts(
        self,
        artifact: str,
        artifacts: List[str],
        scope_by_artifact: Dict[str, str],
    ) -> List[str]:
        code_artifacts = [
            candidate
            for candidate in artifacts
            if not self.compiler._is_test_artifact(candidate)
        ]
        focus = self._test_focus_scope(artifact)
        if focus in {"integration", "regression"}:
            return code_artifacts
        selected = [
            candidate
            for candidate in code_artifacts
            if scope_by_artifact.get(candidate) == focus
        ]
        if selected:
            return selected
        return code_artifacts

    def _test_ownership(self, scope_by_artifact: Dict[str, str]) -> Dict[str, Any]:
        ownership: Dict[str, List[str]] = {}
        for artifact, scope_id in scope_by_artifact.items():
            if not self.compiler._is_test_artifact(artifact):
                continue
            ownership.setdefault(scope_id or "tests", []).append(artifact)
        return {"scope_tests": ownership, "policy": "scope_or_tests_scope"}

    def _scope_id_for_artifact(self, artifact: str) -> str:
        normalized = self._normalize_artifact_path(artifact)
        segments = [segment for segment in normalized.split("/") if segment]
        name = os.path.basename(normalized).lower()
        if self.compiler._is_test_artifact(normalized):
            return self._scope_for_test_artifact(normalized)
        if name in {"__init__.py"}:
            return "package"
        if name in {"main.py", "cli.py", "__main__.py"} or "cli" in segments:
            return "interface"
        searchable_segments = segments[1:-1] if len(segments) > 1 else []
        for segment in searchable_segments:
            scope = self._functional_scope_for_token(segment)
            if scope:
                return scope
        stem = os.path.splitext(name)[0]
        for token in self._name_tokens(stem):
            scope = self._functional_scope_for_token(token)
            if scope:
                return scope
        if len(segments) == 1:
            return "app"
        return "core"

    def _scope_order(self, scope_ids: Iterable[str]) -> List[str]:
        preferred = FUNCTIONAL_SCOPE_ORDER
        unique = sorted(set(scope_ids))
        return [scope for scope in preferred if scope in unique] + [scope for scope in unique if scope not in preferred]

    @staticmethod
    def _canonical_scope_id(scope_id: str) -> str:
        return FUNCTIONAL_SCOPE_ALIASES.get(scope_id, scope_id)

    def _scope_for_test_artifact(self, artifact: str) -> str:
        focus = self._test_focus_scope(artifact)
        if focus in {"integration", "regression", "e2e", "end_to_end"}:
            return "tests"
        scope = self._functional_scope_for_token(focus)
        if scope:
            return scope
        for token in self._name_tokens(focus):
            scope = self._functional_scope_for_token(token)
            if scope:
                return scope
        return "tests"

    @staticmethod
    def _functional_scope_for_token(token: str) -> str:
        normalized = token.lower().replace("-", "_").strip("_")
        return FUNCTIONAL_SCOPE_ALIASES.get(normalized, "")

    @staticmethod
    def _name_tokens(value: str) -> List[str]:
        return [token for token in re.split(r"[_\-.]+", value.lower()) if token]

    @staticmethod
    def _normalize_artifact_path(artifact: str) -> str:
        return os.path.normpath(artifact).replace("\\", "/").strip("/")

    @staticmethod
    def _scope_type(scope_id: str) -> str:
        if scope_id == "package":
            return "package"
        if scope_id == "tests":
            return "tests"
        if scope_id == "data":
            return "data"
        return "code_module"

    @staticmethod
    def _scope_label(scope_id: str) -> str:
        labels = {
            "domain": "Domain models, state, resources, and schemas",
            "core": "Core simulation, rules, and systems",
            "planning": "Planning, policies, heuristics, and recommendations",
            "ai": "AI and planning",
            "io": "Persistence and scenario IO",
            "interface": "Command-line, API, or UI interface",
            "package": "Package scaffolding and metadata",
            "tests": "Automated tests",
            "integration": "Final integration",
        }
        return labels.get(scope_id, f"{scope_id.title()} scope")

    @staticmethod
    def _package_roots(python_artifacts: List[str]) -> List[str]:
        roots: List[str] = []
        for artifact in python_artifacts:
            segments = artifact.split("/")
            if len(segments) > 1 and segments[0].isidentifier() and segments[0] not in roots:
                roots.append(segments[0])
        return roots

    @staticmethod
    def _is_save_load_artifact(artifact: str) -> bool:
        lower = artifact.lower()
        return any(marker in lower for marker in ("save_load", "save-load", "persistence", "storage"))

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        for value in values:
            if value and value not in out:
                out.append(value)
        return out


class ContractCompiler:
    """Normalize, enrich, and validate ContractSpec V8 contracts."""

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    def compile(
        self,
        goal: str,
        draft: Optional[ContractSpec | Dict[str, Any]] = None,
    ) -> ContractSpec:
        if draft is None:
            contract = self._minimal_contract(goal)
        elif isinstance(draft, ContractSpec):
            contract = draft
        else:
            contract = ContractSpec.from_mapping(draft)

        contract = self._normalize_contract(goal, contract)
        contract.validate()
        return contract

    def replan(
        self,
        goal: str,
        current: ContractSpec,
        feedback: str,
    ) -> ContractSpec:
        """Create a new contract revision after runtime evidence exposes a problem.

        This MVP keeps the shape of the existing contract, records the feedback,
        and records diagnostic feedback. Runtime state decides which blocked items
        are reopened. Future planners can replace this with
        an LLM-generated draft followed by the same compile/validate path.
        """

        payload = current.to_record()
        revision = int(payload.get("metadata", {}).get("revision", 0) or 0) + 1
        payload["metadata"] = {
            **dict(payload.get("metadata", {})),
            "revision": revision,
            "planner_mode": "replan",
            "replan_feedback": str(feedback or "").strip(),
        }
        return self.compile(goal, payload)

    def _minimal_contract(self, goal: str) -> ContractSpec:
        planned = self._auto_plan(goal)
        if planned is not None:
            return planned

        profile = self._planning_profile(goal, domain="general", artifact_count=1)
        item = WorkItem(
            id="work:main",
            kind="doc",
            title="Complete requested goal",
            owner_profile="Project_Manager",
            scope_id="root",
            status="READY",
            inputs={"goal": goal},
            target_artifacts=[".contractcoding/output.md"],
            acceptance_criteria=["The requested goal is completed and evidence is recorded."],
            conflict_keys=["artifact:.contractcoding/output.md"],
            execution_mode="auto",
        )
        return ContractSpec(
            goals=[goal],
            work_scopes=[default_root_scope()],
            work_items=[item],
            acceptance_criteria=["The run produces the requested result with recorded evidence."],
            execution_policy={
                "max_parallel_teams": profile.max_parallel_teams,
                "max_parallel_items_per_team": profile.max_parallel_items_per_team,
                "default_execution_plane": "sandbox",
                "context_max_chars": profile.context_max_chars,
                "autonomy_guardrails": self._guardrails(profile),
            },
            risk_policy={"ops_default": "approval_required"},
            metadata={
                "planner": "deterministic-mvp",
                "planning_pipeline": ["goal_strategist_draft", "contract_compiler_validate"],
                "planning_profile": profile.to_record(),
            },
        )

    def _auto_plan(self, goal: str) -> Optional[ContractSpec]:
        if self._looks_like_eval(goal):
            return self._eval_contract(goal)
        if self._looks_like_strong_coding(goal):
            return self._coding_contract(goal)
        if self._looks_like_ops(goal):
            return self._single_artifact_contract(goal, kind="ops", scope_type="ops", artifact=".contractcoding/ops_plan.md")
        if self._looks_like_data(goal):
            return self._single_artifact_contract(goal, kind="data", scope_type="data", artifact=".contractcoding/data_report.md")
        if self._looks_like_research(goal):
            return self._research_contract(goal)
        if self._looks_like_doc(goal):
            return self._single_artifact_contract(goal, kind="doc", scope_type="doc", artifact=".contractcoding/output.md")
        if self._looks_like_coding(goal):
            return self._coding_contract(goal)
        return None

    def _looks_like_eval(self, goal: str) -> bool:
        lower = goal.lower()
        stripped = lower.strip()
        if stripped.startswith(("eval ", "eval:", "evaluate ", "evaluate:", "benchmark ", "benchmark:")):
            return True
        return any(
            marker in lower
            for marker in (
                "completion rate",
                "failure analysis",
                "evaluate completion",
                "evaluate run",
                "evaluate the run",
                "benchmark result",
                "benchmark the",
            )
        )

    def _looks_like_coding(self, goal: str) -> bool:
        lower = goal.lower()
        if self._extract_artifact_paths(goal, CODE_EXTENSIONS):
            return True
        coding_markers = {
            "code",
            "coding",
            "implement",
            "build",
            "create",
            "generate",
            "fix",
            "refactor",
            "python",
            "javascript",
            "typescript",
            "cli",
            "game",
            "app",
            "module",
            "package",
            "utility",
            "utils",
            "生成",
            "创建",
            "项目",
            "小游戏",
        }
        return any(marker in lower for marker in coding_markers)

    def _looks_like_strong_coding(self, goal: str) -> bool:
        lower = goal.lower()
        if self._extract_artifact_paths(goal, CODE_EXTENSIONS):
            return True
        strong_markers = {
            "python package",
            "dependency-free python",
            "package named",
            "package called",
            "unittest",
            "test suite",
            "compileall",
            "importable",
            "cli",
            "repl",
            "module",
            "modules",
            "code",
            "coding",
            "implement",
            "refactor",
        }
        return any(marker in lower for marker in strong_markers)

    def _looks_like_research(self, goal: str) -> bool:
        lower = goal.lower()
        return any(marker in lower for marker in ("research", "paper", "论文", "survey", "outline", "write up"))

    def _looks_like_doc(self, goal: str) -> bool:
        lower = goal.lower()
        return any(marker in lower for marker in ("document", "doc", "readme", "spec", "proposal", "draft", "write", "文档"))

    def _looks_like_data(self, goal: str) -> bool:
        lower = goal.lower()
        return any(marker in lower for marker in ("data", "dataset", "csv", "spreadsheet", "analysis", "analyze", "数据"))

    def _looks_like_ops(self, goal: str) -> bool:
        lower = goal.lower()
        return bool(re.search(r"\b(ops|deploy|deployment|shell|infra|migration|migrate)\b", lower)) or "运维" in lower

    def _coding_contract(self, goal: str) -> ContractSpec:
        artifacts = self._infer_coding_artifacts(goal)
        profile = self._planning_profile(goal, domain="coding", artifact_count=len(artifacts))
        large_planner = LargeProjectPlanner(self)
        if large_planner.should_plan(goal, artifacts):
            return large_planner.build(goal, artifacts, profile)
        code_artifacts = [artifact for artifact in artifacts if not self._is_test_artifact(artifact)]
        test_artifacts = [artifact for artifact in artifacts if self._is_test_artifact(artifact)]
        work_artifacts = code_artifacts or artifacts
        scope_id = self._infer_code_scope(goal, artifacts)
        scope = WorkScope(
            id=scope_id,
            type="code_module",
            label=self._scope_label(goal, scope_id),
            execution_plane_policy="auto",
            team_policy={"team_kind": "coding", "workspace_plane": "worktree"},
            promotion_policy={"mode": "after_team_gate"},
            interface_stability="stable",
            artifacts=list(artifacts),
            conflict_keys=[f"scope:{scope_id}", *[f"artifact:{artifact}" for artifact in artifacts]],
            interfaces=[
                {
                    "type": "scope_artifacts",
                    "scope": scope_id,
                    "artifacts": list(artifacts),
                }
            ],
            verification_policy={
                "layers": ["self_check", "team_gate"],
                "team_gate_required": True,
                "team_gate_id": f"team:{scope_id}",
            },
            test_ownership={
                "owned_tests": [artifact for artifact in artifacts if self._is_test_artifact(artifact)],
            },
        )
        items = []
        code_item_ids = []
        for artifact in work_artifacts:
            item_id = f"coding:{artifact}"
            code_item_ids.append(item_id)
            items.append(
                WorkItem(
                    id=item_id,
                    kind="coding",
                    title=f"Implement {artifact}",
                    owner_profile=self._owner_for_artifact(artifact, goal),
                    module=scope_id,
                    scope_id=scope_id,
                    status="READY",
                    inputs={"goal": goal, "auto_planned": True},
                    target_artifacts=[artifact],
                    acceptance_criteria=self._criteria_for_artifact(artifact, goal),
                    conflict_keys=[f"artifact:{artifact}"],
                    depends_on=[],
                    provided_interfaces=self._provided_interfaces_for_artifact(artifact, goal),
                    required_interfaces=self._required_interfaces_for_artifact(artifact, goal),
                    dependency_policy=self._dependency_policy_for_artifact(artifact, goal),
                    team_role_hint="implementation_worker",
                )
            )

        item_ids = {item.id for item in items}
        normalized = []
        for item in items:
            artifact = item.target_artifacts[0]
            if scope_id == "game" and artifact == "main.py":
                payload = item.to_record()
                payload["depends_on"] = [
                    item_id
                    for item_id in (
                        "coding:game_engine.py",
                        "coding:ai_player.py",
                        "coding:board_generator.py",
                        "coding:terminal_ui.py",
                    )
                    if item_id in item_ids
                ]
                normalized.append(WorkItem.from_mapping(payload))
            else:
                normalized.append(item)

        final_gate = self._contract_final_gate(goal, artifacts)

        return ContractSpec(
            goals=[goal],
            work_scopes=[
                default_root_scope(),
                scope,
            ],
            work_items=[*normalized],
            team_gates=[
                TeamGateSpec(
                    scope_id=scope_id,
                    test_artifacts=test_artifacts,
                    test_plan={
                        "required_public_interfaces": code_artifacts,
                        "test_artifacts": test_artifacts,
                        "required_behaviors": self._criteria_for_team_gate(scope_id, goal),
                    },
                )
            ],
            final_gate=final_gate,
            acceptance_criteria=[
                "All generated code artifacts satisfy their WorkItem acceptance criteria.",
                "Target artifacts are present, importable or runnable where applicable, and contain no placeholder behavior.",
                "Interface boundaries declared in the contract are respected by dependent work items.",
            ],
            execution_policy={
                "max_parallel_teams": profile.max_parallel_teams,
                "max_parallel_items_per_team": profile.max_parallel_items_per_team,
                "default_execution_plane": "sandbox",
                "context_max_chars": profile.context_max_chars,
                "autonomy_guardrails": self._guardrails(profile),
            },
            risk_policy={"ops_default": "approval_required"},
            verification_policy={
                "layers": ["self_check", "team_gate", "final_gate"],
                "self_check": "implicit runtime check for each artifact-level WorkItem",
                "team_gate": "first-class code scope gate",
                "final_gate": "first-class project integration gate",
            },
            test_ownership={"scope_tests": {scope_id: [artifact for artifact in artifacts if self._is_test_artifact(artifact)]}},
            metadata={
                "planner": "deterministic-mvp",
                "planning_pipeline": [
                    "goal_strategist_draft",
                    "contract_compiler_validate",
                    "team_gate",
                    "final_gate",
                ],
                "planning_profile": profile.to_record(),
            },
        )

    def _contract_final_gate(self, goal: str, artifacts: List[str]) -> FinalGateSpec:
        python_artifacts = [artifact for artifact in artifacts if artifact.endswith(".py")]
        return FinalGateSpec(
            required_artifacts=artifacts,
            python_artifacts=python_artifacts,
            package_roots=LargeProjectPlanner._package_roots(python_artifacts),
            requires_tests=any(self._is_test_artifact(artifact) for artifact in artifacts),
            final_acceptance_scenarios=LargeProjectPlanner._final_acceptance_scenarios(goal),
            product_behavior=LargeProjectPlanner._product_behavior_contract(goal, artifacts),
        )

    @staticmethod
    def _final_acceptance_scenarios(goal: str) -> List[Dict[str, Any]]:
        return LargeProjectPlanner._final_acceptance_scenarios(goal)

    @staticmethod
    def _criteria_for_team_gate(scope_id: str, goal: str) -> List[str]:
        if scope_id == "game":
            return ["legal state transitions", "win/loss/draw behavior", "CLI smoke"]
        if scope_id == "utils":
            return ["public utility behavior", "edge cases", "error handling"]
        return ["scope public API behavior", "edge cases", "artifact acceptance criteria"]

    def _research_contract(self, goal: str) -> ContractSpec:
        profile = self._planning_profile(goal, domain="research", artifact_count=2)
        research_scope = WorkScope(
            id="research",
            type="research",
            label="Research and evidence",
            execution_plane_policy="read-only",
            team_policy={"team_kind": "research", "workspace_plane": "read-only"},
            promotion_policy={"mode": "artifact"},
        )
        doc_scope = WorkScope(
            id="writing",
            type="doc",
            label="Synthesis artifact",
            execution_plane_policy="sandbox",
            team_policy={"team_kind": "doc", "workspace_plane": "sandbox"},
            promotion_policy={"mode": "artifact"},
        )
        research = WorkItem(
            id="research:source-notes",
            kind="research",
            title="Gather source-backed notes",
            owner_profile="Researcher",
            scope_id="research",
            status="READY",
            inputs={"goal": goal, "auto_planned": True},
            target_artifacts=[".contractcoding/research_notes.md"],
            acceptance_criteria=[
                "Relevant facts, uncertainty, and source identifiers are summarized.",
                "Claims distinguish evidence from assumptions.",
            ],
            conflict_keys=["artifact:.contractcoding/research_notes.md"],
            execution_mode="read-only",
        )
        doc = WorkItem(
            id="doc:final-artifact",
            kind="doc",
            title="Write final artifact",
            owner_profile="Technical_Writer",
            scope_id="writing",
            depends_on=[research.id],
            status="PENDING",
            inputs={"goal": goal, "auto_planned": True},
            target_artifacts=[".contractcoding/output.md"],
            acceptance_criteria=[
                "The final artifact addresses the requested task.",
                "Research-backed claims are traceable to the research notes.",
            ],
            conflict_keys=["artifact:.contractcoding/output.md"],
        )
        return ContractSpec(
            goals=[goal],
            work_scopes=[default_root_scope(), research_scope, doc_scope],
            work_items=[research, doc],
            acceptance_criteria=["The requested research/writing task is completed with recorded evidence."],
            execution_policy={
                "max_parallel_teams": profile.max_parallel_teams,
                "max_parallel_items_per_team": profile.max_parallel_items_per_team,
                "default_execution_plane": "sandbox",
                "context_max_chars": profile.context_max_chars,
                "autonomy_guardrails": self._guardrails(profile),
            },
            risk_policy={"ops_default": "approval_required"},
            metadata={
                "planner": "deterministic-mvp",
                "planning_pipeline": ["goal_strategist_draft", "contract_compiler_validate"],
                "planning_profile": profile.to_record(),
            },
        )

    def _eval_contract(self, goal: str) -> ContractSpec:
        profile = self._planning_profile(goal, domain="eval", artifact_count=1)
        scope = WorkScope(
            id="eval",
            type="custom",
            label="Evaluation and failure analysis",
            execution_plane_policy="sandbox",
            team_policy={"team_kind": "eval", "workspace_plane": "sandbox"},
            promotion_policy={"mode": "none"},
        )
        item = WorkItem(
            id="eval:run-summary",
            kind="eval",
            title="Evaluate run or task suite",
            owner_profile="Evaluator",
            scope_id="eval",
            status="READY",
            inputs={"goal": goal, "auto_planned": True},
            target_artifacts=[".contractcoding/eval_report.json"],
            acceptance_criteria=[
                "Records concise metrics for completion, tests, security, replan count, and failure category.",
                "Distinguishes observed evidence from assumptions.",
                "Keeps the final eval report short enough to scan.",
            ],
            conflict_keys=["artifact:.contractcoding/eval_report.json"],
            execution_mode="auto",
        )
        return ContractSpec(
            goals=[goal],
            work_scopes=[default_root_scope(), scope],
            work_items=[item],
            acceptance_criteria=["The evaluation report is concise, reproducible, and evidence-backed."],
            execution_policy={
                "max_parallel_teams": min(2, profile.max_parallel_teams),
                "max_parallel_items_per_team": min(2, profile.max_parallel_items_per_team),
                "default_execution_plane": "sandbox",
                "context_max_chars": profile.context_max_chars,
                "autonomy_guardrails": self._guardrails(profile),
            },
            risk_policy={"ops_default": "approval_required"},
            metadata={
                "planner": "deterministic-mvp",
                "planning_pipeline": ["goal_strategist_draft", "contract_compiler_validate"],
                "planning_profile": profile.to_record(),
            },
        )

    def _single_artifact_contract(
        self,
        goal: str,
        *,
        kind: str,
        scope_type: str,
        artifact: str,
    ) -> ContractSpec:
        profile = self._planning_profile(goal, domain=kind, artifact_count=1)
        scope_id = scope_type if scope_type != "doc" else "writing"
        labels = {
            "doc": "Document artifact",
            "data": "Data analysis artifact",
            "ops": "Operational plan and evidence",
        }
        owner_by_kind = {
            "doc": "Technical_Writer",
            "data": "Data_Worker",
            "ops": "Operator",
        }
        criteria_by_kind = {
            "doc": [
                "The document directly addresses the requested goal with a clear structure.",
                "Claims, recommendations, and open questions are distinguishable.",
                "The artifact is concise enough for a human reviewer to scan.",
            ],
            "data": [
                "The artifact records inputs, assumptions, transformations, and sanity checks.",
                "Reported metrics or findings are reproducible from the described data shape.",
                "Schema, row-count, or statistical caveats are explicit when relevant.",
            ],
            "ops": [
                "The artifact describes a dry-run first plan, expected effects, rollback notes, and required approvals.",
                "No destructive operation is executed without explicit approval evidence.",
                "The final evidence separates proposed commands from executed commands.",
            ],
        }
        scope = WorkScope(
            id=scope_id,
            type=scope_type,
            label=labels.get(kind, f"{kind.title()} artifact"),
            artifacts=[artifact],
            conflict_keys=[f"scope:{scope_id}", f"artifact:{artifact}"],
            execution_plane_policy="approval-required" if kind == "ops" else "sandbox",
            team_policy=self._default_team_policy(WorkScope(id=scope_id, type=scope_type)),
            promotion_policy=self._default_promotion_policy(WorkScope(id=scope_id, type=scope_type)),
            verification_policy={
                "layers": ["self_check", "team_gate"],
                "team_gate_required": True,
                "team_gate_id": f"team:{scope_id}",
            },
        )
        item = WorkItem(
            id=f"{kind}:main",
            kind=kind,
            title=f"Complete {kind} deliverable",
            owner_profile=owner_by_kind.get(kind, "Backend_Engineer"),
            scope_id=scope_id,
            status="READY",
            inputs={"goal": goal, "auto_planned": True},
            target_artifacts=[artifact],
            acceptance_criteria=criteria_by_kind.get(
                kind,
                ["The requested deliverable is complete and evidence is recorded."],
            ),
            conflict_keys=[f"artifact:{artifact}"],
            execution_mode="serial" if kind == "ops" else "auto",
            serial_group=f"ops:{scope_id}" if kind == "ops" else "",
            risk_level="high" if kind == "ops" else "medium",
            recovery_policy={"failure_kind": "human_required"} if kind == "ops" else {},
        )
        return ContractSpec(
            goals=[goal],
            work_scopes=[default_root_scope(), scope],
            work_items=[item],
            team_gates=[
                TeamGateSpec(
                    scope_id=scope_id,
                    test_plan={
                        "required_public_interfaces": [artifact],
                        "required_behaviors": [f"{kind} acceptance criteria"],
                    },
                )
            ],
            final_gate=FinalGateSpec(
                required_artifacts=[artifact],
                requires_tests=False,
                final_acceptance_scenarios=self._final_acceptance_scenarios(goal),
                product_behavior=LargeProjectPlanner._product_behavior_contract(goal, [artifact]),
            ),
            acceptance_criteria=[f"The {kind} task is completed with recorded evidence and verification."],
            execution_policy={
                "max_parallel_teams": min(2, profile.max_parallel_teams),
                "max_parallel_items_per_team": min(2, profile.max_parallel_items_per_team),
                "default_execution_plane": "sandbox",
                "context_max_chars": profile.context_max_chars,
                "autonomy_guardrails": self._guardrails(profile),
            },
            risk_policy={"ops_default": "approval_required"},
            verification_policy={
                "layers": ["self_check", "team_gate", "final_gate"],
                "self_check": "implicit runtime check for each artifact-level WorkItem",
                "team_gate": f"first-class {kind} team gate",
                "final_gate": "first-class final artifact coverage gate",
            },
            metadata={
                "planner": "deterministic-mvp",
                "planning_pipeline": ["goal_strategist_draft", "contract_compiler_validate"],
                "planning_profile": profile.to_record(),
            },
        )

    @staticmethod
    def _guardrails(profile: PlanningProfile) -> Dict[str, int]:
        return {
            "infra_retry_limit": 2,
            "item_repair_limit": max(1, profile.recovery_limit),
            "test_repair_limit": max(4, profile.recovery_limit + 1),
            "contract_replan_limit": max(1, profile.recovery_limit),
        }

    def _planning_profile(self, goal: str, domain: str, artifact_count: int) -> PlanningProfile:
        lower = goal.lower()
        simple_markers = ("simple", "easy", "small", "tiny", "basic", "简单", "容易")
        hard_markers = (
            "hard",
            "difficult",
            "advanced",
            "complex",
            "large",
            "production",
            "minesweeper",
            "sokoban",
            "roguelike",
            "困难",
            "复杂",
            "大型",
        )
        if any(marker in lower for marker in hard_markers) or artifact_count >= 5:
            return PlanningProfile(
                domain=domain,
                complexity="hard",
                max_parallel_teams=4,
                max_parallel_items_per_team=3,
                context_max_chars=20000,
                recovery_limit=2,
                rationale="Multiple artifacts or hard markers imply scoped teams and stronger verification.",
            )
        if any(marker in lower for marker in simple_markers) and artifact_count <= 3:
            return PlanningProfile(
                domain=domain,
                complexity="simple",
                max_parallel_teams=2,
                max_parallel_items_per_team=2,
                context_max_chars=10000,
                recovery_limit=1,
                rationale="Small goal and few artifacts imply a compact contract.",
            )
        return PlanningProfile(
            domain=domain,
            complexity="medium",
            max_parallel_teams=3,
            max_parallel_items_per_team=3,
            context_max_chars=16000,
            recovery_limit=2,
            rationale="Default balanced profile inferred from requested deliverables.",
        )

    def _infer_coding_artifacts(self, goal: str) -> List[str]:
        explicit = self._extract_artifact_paths(goal, CODE_EXTENSIONS)
        if explicit:
            return explicit

        lower = goal.lower()
        package_name = self._infer_python_package_name(goal)
        if package_name and any(marker in lower for marker in LARGE_PROJECT_MARKERS):
            return self._default_large_package_artifacts(package_name)

        if "utility" in lower or "utilities" in lower or "utils" in lower:
            if "text" in lower and "math" in lower:
                return ["math_tools.py", "text_tools.py"]
            if "two" in lower or "2" in lower or "independent" in lower:
                return ["math_tools.py", "text_tools.py"]
            return ["utils.py", "test_utils.py"]
        if "game" in lower or "小游戏" in lower:
            game_kind = self._game_kind(goal)
            complexity = self._game_complexity(goal)
            if game_kind in {"guessing", "rps"} or complexity == "simple":
                return ["game_engine.py", "main.py", "test_game_engine.py"]
            if game_kind in {"minesweeper", "sokoban"} or complexity == "hard":
                return ["game_engine.py", "board_generator.py", "main.py", "test_game_engine.py"]
            return ["game_engine.py", "ai_player.py", "main.py", "test_game_engine.py"]
        if "cli" in lower:
            return ["main.py", "test_main.py"]
        if "frontend" in lower or "react" in lower:
            return ["src/App.tsx", "src/styles.css"]
        return ["main.py", "test_main.py"]

    @staticmethod
    def _infer_python_package_name(goal: str) -> str:
        patterns = (
            r"\bpackage\s+(?:named|called)\s+([A-Za-z_][\w]*)",
            r"\bpython\s+package\s+([A-Za-z_][\w]*)",
            r"\bnamed\s+([A-Za-z_][\w]*)\b",
            r"\bcalled\s+([A-Za-z_][\w]*)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, goal, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1).strip().lower()
            if candidate in {"package", "project", "app", "application", "toolkit"}:
                continue
            if re.match(r"^[a-z_][a-z0-9_]*$", candidate):
                return candidate
        return ""

    @staticmethod
    def _default_large_package_artifacts(package_name: str) -> List[str]:
        modules = [
            "__init__.py",
            "domain/resources.py",
            "domain/citizens.py",
            "domain/buildings.py",
            "domain/districts.py",
            "domain/laws.py",
            "domain/events.py",
            "domain/disasters.py",
            "domain/technology.py",
            "domain/factions.py",
            "domain/invariants.py",
            "core/engine.py",
            "core/turns.py",
            "core/economy.py",
            "core/construction.py",
            "core/research.py",
            "core/logistics.py",
            "core/diplomacy.py",
            "core/quests.py",
            "core/scoring.py",
            "core/victory.py",
            "ai/planner.py",
            "ai/policies.py",
            "ai/heuristics.py",
            "io/save_load.py",
            "io/scenarios.py",
            "io/maps.py",
            "interface/cli.py",
            "interface/repl.py",
            "tests/test_domain.py",
            "tests/test_engine.py",
            "tests/test_ai.py",
            "tests/test_io.py",
            "tests/test_interface.py",
            "tests/test_integration.py",
        ]
        return [f"{package_name}/{module}" if not module.startswith("tests/") else module for module in modules]

    def _extract_artifact_paths(self, goal: str, extensions: set[str]) -> List[str]:
        paths: List[str] = []
        pattern = re.compile(r"(?<![\w/.-])([\w./-]+(?:{}))(?![\w/-])".format("|".join(re.escape(ext) for ext in extensions)))
        for match in pattern.finditer(goal):
            path = match.group(1).strip("./")
            if not path or path.startswith(".."):
                continue
            normalized = os.path.normpath(path).replace("\\", "/")
            if normalized not in paths:
                paths.append(normalized)
        return paths

    def _infer_code_scope(self, goal: str, artifacts: List[str]) -> str:
        lower = goal.lower()
        if "utility" in lower or "utils" in lower:
            return "utils"
        if "game" in lower or "小游戏" in lower:
            return "game"
        if "frontend" in lower or any(artifact.endswith((".tsx", ".jsx", ".css", ".html")) for artifact in artifacts):
            return "frontend"
        if len({artifact.split("/", 1)[0] for artifact in artifacts if "/" in artifact}) == 1:
            return artifacts[0].split("/", 1)[0]
        return "app"

    def _scope_label(self, goal: str, scope_id: str) -> str:
        if scope_id == "utils":
            return "Utility code module"
        if scope_id == "game":
            return "Game implementation module"
        if scope_id == "frontend":
            return "Frontend implementation module"
        return f"{scope_id.title()} implementation module"

    def _game_kind(self, goal: str) -> str:
        lower = goal.lower()
        if "guess" in lower or "number guessing" in lower:
            return "guessing"
        if "rock paper scissors" in lower or "rps" in lower:
            return "rps"
        if "tic tac toe" in lower or "tictactoe" in lower or "nought" in lower:
            return "tic_tac_toe"
        if "connect four" in lower or "connect 4" in lower:
            return "connect_four"
        if "mine" in lower or "minesweeper" in lower:
            return "minesweeper"
        if "sokoban" in lower or "warehouse" in lower:
            return "sokoban"
        return "generic"

    def _game_complexity(self, goal: str) -> str:
        lower = goal.lower()
        if any(marker in lower for marker in ("medium", "moderate", "intermediate", "中等")):
            return "medium"
        if any(marker in lower for marker in ("hard", "difficult", "advanced", "complex", "minesweeper", "sokoban", "roguelike", "困难", "复杂")):
            return "hard"
        if any(marker in lower for marker in ("simple", "easy", "guess", "rock paper scissors", "rps", "简单")):
            return "simple"
        return "medium"

    def _provided_interfaces_for_artifact(self, artifact: str, goal: str) -> List[Dict[str, Any]]:
        if artifact == "math_tools.py":
            return [
                {"type": "function", "name": "add", "signature": "add(a, b)"},
                {"type": "function", "name": "subtract", "signature": "subtract(a, b)"},
                {"type": "function", "name": "multiply", "signature": "multiply(a, b)"},
                {"type": "function", "name": "divide", "signature": "divide(a, b)"},
            ]
        if artifact == "text_tools.py":
            return [
                {"type": "function", "name": "slugify", "signature": "slugify(text)"},
                {"type": "function", "name": "word_count", "signature": "word_count(text)"},
            ]
        if artifact == "game_engine.py":
            game_kind = self._game_kind(goal)
            if game_kind == "guessing":
                return [
                    {
                        "type": "class",
                        "name": "GuessingGame",
                        "attributes": ["secret", "minimum", "maximum", "max_attempts", "attempts_used", "is_over"],
                        "methods": [
                            "guess(value: int) -> GuessResult",
                            "attempts_remaining() -> int",
                            "validate_guess(value: int) -> None",
                        ],
                    },
                    {
                        "type": "class",
                        "name": "GuessResult",
                        "attributes": ["status", "message", "attempts_remaining", "is_correct", "is_game_over"],
                    },
                    {"type": "exception", "name": "InvalidGuessError"},
                ]
            if game_kind == "tic_tac_toe":
                return [
                    {
                        "type": "class",
                        "name": "TicTacToe",
                        "attributes": ["board", "current_player"],
                        "methods": [
                            "legal_moves() -> list[int]",
                            "make_move(position: int) -> None",
                            "winner() -> str | None",
                            "is_draw() -> bool",
                            "is_game_over() -> bool",
                            "clone() -> TicTacToe",
                            "render() -> str",
                        ],
                    },
                    {"type": "constants", "names": ["EMPTY", "PLAYER_X", "PLAYER_O"]},
                    {"type": "exception", "name": "InvalidMoveError"},
                ]
            if game_kind == "minesweeper":
                return [
                    {
                        "type": "class",
                        "name": "MinesweeperGame",
                        "attributes": ["rows", "columns", "mine_count", "cells", "state"],
                        "methods": [
                            "reveal(row: int, column: int) -> RevealResult",
                            "toggle_flag(row: int, column: int) -> None",
                            "neighbors(row: int, column: int) -> list[tuple[int, int]]",
                            "is_won() -> bool",
                            "is_lost() -> bool",
                            "render(show_mines: bool = False) -> str",
                        ],
                    },
                    {"type": "class", "name": "Cell", "attributes": ["has_mine", "revealed", "flagged", "adjacent_mines"]},
                    {"type": "exception", "name": "InvalidMoveError"},
                ]
            return [
                {
                    "type": "class",
                    "name": "ConnectFour",
                    "attributes": ["board", "current_player"],
                    "methods": [
                        "clone() -> ConnectFour",
                        "copy() -> ConnectFour",
                        "is_legal_move(column: int) -> bool",
                        "legal_moves() -> list[int]",
                        "drop_piece(column: int) -> int",
                        "switch_player() -> str",
                        "winner() -> str | None",
                        "check_winner() -> str | None",
                        "is_draw() -> bool",
                        "is_game_over() -> bool",
                        "render() -> str",
                    ],
                },
                {
                    "type": "constants",
                    "names": ["ROWS", "COLUMNS", "EMPTY", "PLAYER_ONE", "PLAYER_TWO"],
                },
                {"type": "exception", "name": "InvalidMoveError"},
            ]
        if artifact == "ai_player.py":
            game_kind = self._game_kind(goal)
            if game_kind == "tic_tac_toe":
                return [
                    {
                        "type": "function",
                        "name": "choose_ai_move",
                        "signature": "choose_ai_move(game, ai_player='O') -> int",
                    }
                ]
            return [
                {
                    "type": "function",
                    "name": "choose_ai_move",
                    "signature": "choose_ai_move(game, ai_player='O', depth=4) -> int",
                }
            ]
        if artifact == "board_generator.py":
            return [
                {
                    "type": "function",
                    "name": "generate_mines",
                    "signature": "generate_mines(rows: int, columns: int, mine_count: int, seed: int | None = None, safe_cell: tuple[int, int] | None = None) -> set[tuple[int, int]]",
                }
            ]
        return []

    def _required_interfaces_for_artifact(self, artifact: str, goal: str) -> List[Dict[str, Any]]:
        if artifact == "ai_player.py":
            game_kind = self._game_kind(goal)
            if game_kind == "tic_tac_toe":
                return [
                    {
                        "from": "game_engine.py",
                        "imports": ["TicTacToe", "InvalidMoveError", "PLAYER_X", "PLAYER_O", "EMPTY"],
                    }
                ]
            return [
                {
                    "from": "game_engine.py",
                    "imports": ["COLUMNS", "EMPTY", "PLAYER_ONE", "PLAYER_TWO", "InvalidMoveError"],
                    "uses": ["ConnectFour-compatible game object"],
                }
            ]
        if artifact == "main.py":
            game_kind = self._game_kind(goal)
            if game_kind == "guessing":
                return [
                    {"from": "game_engine.py", "imports": ["GuessingGame", "InvalidGuessError"]},
                ]
            if game_kind == "tic_tac_toe":
                return [
                    {"from": "game_engine.py", "imports": ["TicTacToe", "InvalidMoveError", "PLAYER_X", "PLAYER_O"]},
                    {"from": "ai_player.py", "imports": ["choose_ai_move"]},
                ]
            if game_kind == "minesweeper":
                return [
                    {"from": "game_engine.py", "imports": ["MinesweeperGame", "InvalidMoveError"]},
                    {"from": "board_generator.py", "imports": ["generate_mines"]},
                ]
            return [
                {
                    "from": "game_engine.py",
                    "imports": ["COLUMNS", "PLAYER_ONE", "PLAYER_TWO", "ConnectFour", "InvalidMoveError"],
                },
                {"from": "ai_player.py", "imports": ["choose_ai_move"]},
            ]
        lower_goal = goal.lower()
        if self._is_test_artifact(artifact) and ("game" in lower_goal or "小游戏" in lower_goal):
            game_kind = self._game_kind(goal)
            if game_kind == "guessing":
                return [{"from": "game_engine.py", "imports": ["GuessingGame", "InvalidGuessError"]}]
            if game_kind == "tic_tac_toe":
                return [
                    {"from": "game_engine.py", "imports": ["TicTacToe", "InvalidMoveError"]},
                    {"from": "ai_player.py", "imports": ["choose_ai_move"]},
                ]
            if game_kind == "minesweeper":
                return [
                    {"from": "game_engine.py", "imports": ["MinesweeperGame", "InvalidMoveError"]},
                    {"from": "board_generator.py", "imports": ["generate_mines"]},
                ]
            return [
                {"from": "game_engine.py", "imports": ["ConnectFour", "InvalidMoveError"]},
                {"from": "ai_player.py", "imports": ["choose_ai_move"]},
            ]
        return []

    def _dependency_policy_for_artifact(self, artifact: str, goal: str) -> str:
        lower = goal.lower()
        if artifact == "main.py" and ("game" in lower or "小游戏" in lower):
            return "interface"
        return "done"

    def _owner_for_artifact(self, artifact: str, goal: str) -> str:
        lower = f"{goal} {artifact}".lower()
        if artifact.endswith((".tsx", ".jsx", ".css", ".html")) or "frontend" in lower or "react" in lower:
            return "Frontend_Engineer"
        if "algorithm" in lower or artifact.startswith("test_"):
            return "Algorithm_Engineer" if "algorithm" in lower else "Backend_Engineer"
        return "Backend_Engineer"

    def _criteria_for_artifact(self, artifact: str, goal: str) -> List[str]:
        if artifact == "math_tools.py":
            return [
                "Defines add(a, b), subtract(a, b), multiply(a, b), and divide(a, b).",
                "divide(a, b) raises ValueError when b is zero.",
                "The module is importable with no side effects.",
            ]
        if artifact == "text_tools.py":
            return [
                "Defines slugify(text) and word_count(text).",
                "slugify lowercases text, replaces runs of non-alphanumeric characters with one hyphen, and strips surrounding hyphens.",
                "word_count returns the number of non-empty whitespace-separated words.",
                "The module is importable with no side effects.",
            ]
        if artifact == "game_engine.py":
            game_kind = self._game_kind(goal)
            if game_kind == "guessing":
                return [
                    "Defines a pure GuessingGame engine with configurable secret number, inclusive minimum/maximum range, max attempts, attempts used, and game-over state.",
                    "guess(value) validates integer and range, returns high/low/correct feedback, decrements attempts exactly once for legal guesses, and detects win/loss.",
                    "Invalid out-of-range or non-integer guesses raise InvalidGuessError without consuming attempts.",
                    "Contains no terminal input loop, no random secret generation at import time, no unittest classes, and is importable with no side effects.",
                ]
            if game_kind == "tic_tac_toe":
                return [
                    "Defines a pure TicTacToe engine with a 3x3 board, legal move validation, current-player turn switching, and clone/copy support.",
                    "Detects X/O wins across rows, columns, and diagonals, detects draws, returns no legal moves after terminal states, and rejects occupied/out-of-range/post-game moves with InvalidMoveError.",
                    "Contains no terminal input loop, no AI search, no unittest classes, and is importable with no side effects.",
                ]
            if game_kind == "minesweeper":
                return [
                    "Defines a pure MinesweeperGame engine with configurable rows, columns, mine count, deterministic mine layout support, cells, flags, reveal state, win/loss state, and render().",
                    "Implements reveal rules including mine loss, adjacent mine counts, recursive/flood reveal for zero-adjacent cells, flag/unflag behavior, and first-click-safe setup when requested.",
                    "Rejects invalid coordinates and illegal reveals/flags with InvalidMoveError.",
                    "Contains no terminal input loop, no unittest classes, and is importable with no side effects.",
                ]
            return [
                "Defines a pure Connect Four style engine with ROWS=6, COLUMNS=7, EMPTY='.', PLAYER_ONE='X', PLAYER_TWO='O', and InvalidMoveError.",
                "Defines ConnectFour with a 6x7 board, current_player, clone/copy support, gravity-based drop_piece(column), legal move validation, turn switching, winner detection in horizontal vertical and both diagonal directions, draw detection, and render().",
                "Contains no terminal input loop, no AI search, no unittest classes, and no duplicated CLI behavior.",
                "The module is importable with no side effects.",
            ]
        if artifact == "ai_player.py":
            if self._game_kind(goal) == "tic_tac_toe":
                return [
                    "Imports the public TicTacToe engine API from game_engine.py instead of duplicating board rules.",
                    "Defines choose_ai_move(game, ai_player='O') that always returns a legal zero-based position.",
                    "Uses minimax or equivalent deterministic search to choose immediate wins and blocks.",
                    "The module is importable with no side effects.",
                ]
            return [
                "Imports the public engine API from game_engine.py instead of duplicating board rules or ConnectFour.",
                "Defines choose_ai_move(game, ai_player='O', depth=4) that always returns a legal zero-based column or raises the engine's invalid/no-move error when no legal move exists.",
                "Uses a simple minimax or alpha-beta search with a deterministic center-preferring fallback.",
                "The module is importable with no side effects.",
            ]
        if artifact == "board_generator.py":
            return [
                "Defines deterministic board/mine generation helpers used by the game engine or CLI.",
                "generate_mines(...) returns exactly mine_count unique in-bounds coordinates and excludes the safe_cell when provided.",
                "The module is dependency-free, importable with no side effects, and does not duplicate game state logic.",
            ]
        if artifact == "main.py":
            game_kind = self._game_kind(goal)
            if game_kind == "guessing":
                return [
                    "Provides only the terminal CLI entrypoint for the number guessing game.",
                    "Imports GuessingGame and InvalidGuessError from game_engine.py; it must not duplicate engine rules.",
                    "Accepts basic CLI options for range, max attempts, and optional secret/seed, and handles quit/EOF cleanly.",
                    "The module is importable with no side effects and runs the CLI only under if __name__ == '__main__'.",
                ]
            if game_kind == "tic_tac_toe":
                return [
                    "Provides only the terminal CLI entrypoint for playing Tic Tac Toe against the AI.",
                    "Imports TicTacToe and constants from game_engine.py and choose_ai_move from ai_player.py; it must not duplicate engine or AI logic.",
                    "Handles human input, invalid moves, AI turns, win/draw messages, and quit/EOF cleanly.",
                    "The module is importable with no side effects and runs the CLI only under if __name__ == '__main__'.",
                ]
            if game_kind == "minesweeper":
                return [
                    "Provides only the terminal CLI entrypoint for Minesweeper.",
                    "Imports MinesweeperGame from game_engine.py and board generation helpers from board_generator.py; it must not duplicate engine logic.",
                    "Supports reveal and flag commands, deterministic seed/options, win/loss messages, and quit/EOF cleanly.",
                    "The module is importable with no side effects and runs the CLI only under if __name__ == '__main__'.",
                ]
            return [
                "Provides only the terminal CLI entrypoint for playing against the AI.",
                "Imports ConnectFour and constants from game_engine.py and choose_ai_move from ai_player.py; it must not duplicate the engine or AI implementation.",
                "Accepts basic CLI options such as AI depth and who moves first, and handles quit/EOF cleanly.",
                "The module is importable with no side effects and runs the CLI only under if __name__ == '__main__'.",
            ]
        if self._is_test_artifact(artifact):
            lower_goal = goal.lower()
            if "game" not in lower_goal and "小游戏" not in lower_goal:
                return [
                    f"Contains focused tests for the behavior requested by the contract: {goal}",
                    "Tests can be run with the repository's standard Python test command or direct unittest execution.",
                ]
            game_kind = self._game_kind(goal)
            if game_kind == "guessing":
                return [
                    "Contains focused unittest tests for correct guess, too-low feedback, too-high feedback, invalid out-of-range guess, attempts remaining, and game-over loss.",
                    "Tests can be run with direct unittest execution.",
                ]
            if game_kind == "tic_tac_toe":
                return [
                    "Contains focused unittest tests for legal moves, illegal occupied/out-of-range/post-game moves, row/column/diagonal wins, draw detection, turn switching, and AI choosing a legal winning or blocking move.",
                    "Tests can be run with direct unittest execution.",
                ]
            if game_kind == "minesweeper":
                return [
                    "Contains focused unittest tests for deterministic mine generation, first-click safe behavior, reveal of numbered cells, recursive zero reveal, flag toggling, mine loss, and win detection.",
                    "Tests can be run with direct unittest execution.",
                ]
            return [
                "Contains focused unittest tests for gravity, illegal full-column moves, horizontal vertical and both diagonal wins, draw detection, turn switching, and AI choosing a legal move.",
                "Tests can be run with the repository's standard Python test command or direct unittest execution.",
            ]
        return [
            f"`{artifact}` implements the requested behavior: {goal}",
            "The artifact is complete, runnable or importable where applicable, and contains no placeholder behavior.",
        ]

    @staticmethod
    def _is_test_artifact(artifact: str) -> bool:
        name = os.path.basename(artifact)
        return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{artifact}"

    def _requirements_for_contract(
        self,
        goal: str,
        items: List[WorkItem],
        final_gate: Optional[FinalGateSpec],
    ) -> RequirementSpec:
        delivery = self._delivery_type(items)
        scenarios = list(final_gate.final_acceptance_scenarios if final_gate else [])
        if not scenarios:
            scenarios = LargeProjectPlanner._final_acceptance_scenarios(goal)
        quality_bar = [
            "Contract-declared artifacts exist and pass deterministic checks.",
            "Generated tests exercise real public interfaces rather than mocked project internals.",
            "Final acceptance scenarios pass from the promoted workspace.",
        ]
        if delivery == "coding":
            quality_bar.append("Python code compiles, imports, and passes unittest discovery when tests are required.")
        return RequirementSpec(
            summary=self._task_intent(goal),
            delivery_type=delivery,
            user_flows=self._user_flows_for_goal(goal),
            acceptance_scenarios=scenarios,
            constraints=self._constraints_for_goal(goal),
            non_goals=[
                "Do not optimize for artificial code volume or padding.",
                "Do not invent private APIs in tests when public interfaces are not frozen.",
            ],
            quality_bar=quality_bar,
            ambiguities=[],
            status="FROZEN",
        )

    @staticmethod
    def _user_flows_for_goal(goal: str) -> List[str]:
        lower = goal.lower()
        flows: List[str] = []
        if any(marker in lower for marker in ("cli", "command", "repl", "terminal")):
            flows.append("A user can run the public command-line or REPL entrypoint against a real scenario.")
        if any(marker in lower for marker in ("save", "load", "roundtrip", "scenario")):
            flows.append("A generated scenario can be saved, loaded, and resumed without losing state.")
        if any(marker in lower for marker in ("simulation", "turn", "engine", "game")):
            flows.append("A deterministic simulation/game flow advances through legal state transitions.")
        return flows

    @staticmethod
    def _constraints_for_goal(goal: str) -> List[str]:
        constraints = ["Stay dependency-free unless the user explicitly requested dependencies."]
        lower = goal.lower()
        if "python" in lower:
            constraints.append("Use importable Python modules with no import-time side effects.")
        if "test" in lower or "unittest" in lower:
            constraints.append("Tests must be executable, non-empty, and not all skipped.")
        return constraints

    def _architecture_for_contract(
        self,
        scopes: List[WorkScope],
        items: List[WorkItem],
    ) -> ArchitectureSpec:
        items_by_scope: Dict[str, List[WorkItem]] = {}
        for item in items:
            items_by_scope.setdefault(item.scope_id, []).append(item)
        contexts: List[Dict[str, Any]] = []
        artifacts_by_scope: Dict[str, List[str]] = {}
        for scope in scopes:
            if scope.id in {"root", "integration"}:
                continue
            artifacts = list(scope.artifacts)
            if not artifacts:
                artifacts = [
                    artifact
                    for item in items_by_scope.get(scope.id, [])
                    for artifact in item.target_artifacts
                    if not artifact.startswith(".contractcoding/interfaces/")
                ]
            artifacts_by_scope[scope.id] = ContractSpec._dedupe(artifacts)
            contexts.append(
                {
                    "id": scope.id,
                    "type": scope.type,
                    "label": scope.label,
                    "artifact_count": len(artifacts_by_scope[scope.id]),
                    "state": "DRAFT",
                }
            )
        dependencies: List[Dict[str, Any]] = []
        for scope in scopes:
            if scope.id in {"root", "integration"}:
                continue
            depends = ContractSpec._team_dependencies(scope.id, items_by_scope)
            for dependency in depends:
                dependencies.append({"from": scope.id, "to": dependency})
        return ArchitectureSpec(
            status="DRAFT",
            bounded_contexts=contexts,
            dependency_direction=dependencies,
            artifacts=artifacts_by_scope,
            notes=[
                "Functional team boundaries are draft until interface and gate evidence proves them.",
                "Files and methods remain team-internal batches, not separate teams.",
            ],
        )

    def _ensure_item_phases(self, items: List[WorkItem]) -> List[WorkItem]:
        if not items:
            return []
        has_coding = any(item.kind == "coding" for item in items)
        output: List[WorkItem] = []
        for item in items:
            phase_id = str(item.inputs.get("phase_id", "") or item.context_policy.get("phase_id", "")).strip()
            if not phase_id:
                if item.id.startswith(("scaffold:", "interface:")):
                    phase_id = "vertical_slice"
                elif has_coding:
                    phase_id = "vertical_slice"
                else:
                    phase_id = "single_phase"
            payload = item.to_record()
            payload["inputs"] = {**dict(payload.get("inputs", {})), "phase_id": phase_id}
            output.append(WorkItem.from_mapping(payload))
        return output

    def _default_phase_plan(
        self,
        scopes: List[WorkScope],
        items: List[WorkItem],
    ) -> List[PhaseContract]:
        phase_to_items: Dict[str, List[WorkItem]] = {}
        for item in items:
            phase_id = str(item.inputs.get("phase_id", "") or "single_phase")
            phase_to_items.setdefault(phase_id, []).append(item)
        has_coding = any(item.kind == "coding" for item in items)
        if not has_coding:
            selected = phase_to_items.get("single_phase", items)
            return [
                PhaseContract(
                    phase_id="single_phase",
                    goal="Complete the requested non-coding work and run the appropriate evaluator gate.",
                    mode="parallel" if len({item.scope_id for item in selected}) > 1 else "serial",
                    teams_in_scope=self._dedupe(item.scope_id for item in selected if item.scope_id),
                    deliverables=self._dedupe(
                        artifact
                        for item in selected
                        for artifact in item.target_artifacts
                        if artifact
                    ),
                    phase_gate=PhaseGateSpec(
                        checks=["artifact_presence", "kind_specific_eval"],
                        criteria=["The requested deliverable is produced with evidence."],
                    ),
                )
            ]

        phases: List[PhaseContract] = [
            PhaseContract(
                phase_id="requirements.freeze",
                goal="Freeze the PRD Lite and acceptance scenarios.",
                mode="serial",
                deliverables=[".contractcoding/prd.md"],
                phase_gate=PhaseGateSpec(
                    checks=["prd_projection"],
                    criteria=["Requirements are testable before build work proceeds."],
                ),
            ),
            PhaseContract(
                phase_id="architecture.sketch",
                goal="Sketch functional teams and dependency direction.",
                mode="serial",
                entry_conditions=["requirements.freeze passed"],
                teams_in_scope=[
                    scope.id
                    for scope in scopes
                    if scope.id not in {"root", "integration"}
                ],
                deliverables=[".contractcoding/contract.md"],
                phase_gate=PhaseGateSpec(
                    checks=["bounded_contexts"],
                    criteria=["Team boundaries are functional rather than file-level."],
                ),
            ),
        ]
        for phase_id, phase_items in phase_to_items.items():
            if phase_id in {"requirements.freeze", "architecture.sketch"}:
                continue
            phases.append(
                PhaseContract(
                    phase_id=phase_id,
                    goal=(
                        "Build the minimal real vertical path."
                        if phase_id == "vertical_slice"
                        else f"Execute phase `{phase_id}`."
                    ),
                    mode="hybrid" if phase_id == "vertical_slice" else "parallel",
                    entry_conditions=["architecture.sketch passed"],
                    teams_in_scope=self._dedupe(item.scope_id for item in phase_items if item.scope_id),
                    deliverables=self._dedupe(
                        artifact
                        for item in phase_items
                        for artifact in item.target_artifacts
                        if artifact
                    ),
                    phase_gate=PhaseGateSpec(
                        checks=["self_check", "team_gate"],
                        criteria=["Phase work self-checks before later phases activate."],
                    ),
                )
            )
        phases.append(
            PhaseContract(
                phase_id="final_acceptance",
                goal="Run final deterministic integration checks.",
                mode="serial",
                entry_conditions=["all prior phases passed"],
                teams_in_scope=["integration"],
                deliverables=[".contractcoding/integration_report.json"],
                phase_gate=PhaseGateSpec(
                    checks=["compile_import", "tests", "final_gate"],
                    criteria=["Final gate passes before run completion."],
                ),
            )
        )
        return phases

    def _default_milestones(
        self,
        scopes: List[WorkScope],
        items: List[WorkItem],
    ) -> List[MilestoneSpec]:
        has_coding = any(item.kind == "coding" for item in items)
        if not has_coding:
            return [
                MilestoneSpec("requirements.freeze", "serial", completion_condition="requirements.status == FROZEN"),
                MilestoneSpec("team.build", "parallel", depends_on=["requirements.freeze"]),
                MilestoneSpec("team.gate", "parallel", depends_on=["team.build"]),
                MilestoneSpec("integration.gate", "serial", depends_on=["team.gate"]),
            ]
        return [
            MilestoneSpec(
                "requirements.freeze",
                "serial",
                completion_condition="RequirementSpec is frozen and PRD projection is written.",
            ),
            MilestoneSpec(
                "architecture.sketch",
                "serial",
                depends_on=["requirements.freeze"],
                completion_condition="Functional bounded contexts and coarse artifacts are drafted.",
            ),
            MilestoneSpec(
                "critical.scaffold",
                "hybrid",
                depends_on=["architecture.sketch"],
                completion_condition="Critical public skeletons and scaffold manifests are generated.",
            ),
            MilestoneSpec(
                "critical.interface_tests",
                "hybrid",
                depends_on=["critical.scaffold"],
                completion_condition="Interface conformance targets are recorded before downstream build.",
            ),
            MilestoneSpec(
                "team.interfaces",
                "parallel",
                depends_on=["critical.interface_tests"],
                completion_condition="Each buildable team has frozen team-local interface specs.",
            ),
            MilestoneSpec(
                "team.build",
                "parallel",
                depends_on=["team.interfaces"],
                completion_condition="Ready teams complete implementation WorkItems and self-checks.",
            ),
            MilestoneSpec(
                "team.gate",
                "parallel",
                depends_on=["team.build"],
                completion_condition="Team gates pass interface conformance and behavior checks.",
            ),
            MilestoneSpec(
                "promotion",
                "serial",
                depends_on=["team.gate"],
                completion_condition="Passed teams promote through merge lock.",
            ),
            MilestoneSpec(
                "integration.gate",
                "serial",
                depends_on=["promotion"],
                completion_condition="Final promoted workspace passes deterministic integration gate.",
            ),
            MilestoneSpec(
                "impact.replan",
                "hybrid",
                depends_on=["integration.gate"],
                completion_condition="Only affected contract slices are delta-replanned when diagnostics require it.",
            ),
        ]

    def _interfaces_for_contract(
        self,
        goal: str,
        scopes: List[WorkScope],
        items: List[WorkItem],
        team_gates: List[TeamGateSpec],
        final_gate: Optional[FinalGateSpec],
    ) -> List[InterfaceSpec]:
        interfaces: List[InterfaceSpec] = []
        scope_ids = [
            scope.id
            for scope in scopes
            if scope.id not in {"root", "integration"}
        ]
        for scope in scopes:
            if scope.id in {"root", "integration"}:
                continue
            artifacts = ContractSpec._dedupe(
                [
                    artifact
                    for item in items
                    if item.scope_id == scope.id
                    for artifact in item.target_artifacts
                    if artifact.endswith(".py") and not self._is_test_artifact(artifact)
                    and not artifact.startswith(".contractcoding/interfaces/")
                ]
                or [artifact for artifact in scope.artifacts if artifact.endswith(".py")]
            )
            if not artifacts:
                continue
            critical = self._critical_interface_for_scope(scope.id, artifacts, scope_ids)
            if critical is not None:
                interfaces.append(critical)
            team_local = self._team_local_interface_for_scope(scope.id, artifacts, goal)
            if team_local is not None:
                interfaces.append(team_local)
        return interfaces

    def _critical_interface_for_scope(
        self,
        scope_id: str,
        artifacts: List[str],
        all_scopes: List[str],
    ) -> Optional[InterfaceSpec]:
        consumers_by_scope = {
            "package": [scope for scope in all_scopes if scope != "package"],
            "domain": [scope for scope in all_scopes if scope in {"core", "planning", "ai", "io", "interface", "tests"}],
            "core": [scope for scope in all_scopes if scope in {"planning", "ai", "io", "interface", "tests"}],
            "planning": [scope for scope in all_scopes if scope in {"interface", "tests"}],
            "ai": [scope for scope in all_scopes if scope in {"interface", "tests"}],
            "io": [scope for scope in all_scopes if scope in {"interface", "tests"}],
            "interface": ["tests"] if "tests" in all_scopes else [],
        }
        artifact = self._preferred_interface_artifact(scope_id, artifacts)
        if scope_id not in consumers_by_scope or not artifact:
            return None
        return InterfaceSpec(
            id=f"{scope_id}.critical",
            owner_team=scope_id,
            consumers=consumers_by_scope.get(scope_id, []),
            artifact=artifact,
            symbols=self._symbols_for_artifact(artifact, scope_id, critical=True),
            schemas=self._schemas_for_scope(scope_id),
            semantics=self._semantics_for_scope(scope_id, critical=True),
            status="FROZEN",
            critical=True,
            source_milestone="critical.interface_tests",
            stability="cross-team",
            scaffold={
                "artifact": artifact,
                "manifest": f".contractcoding/scaffolds/{scope_id}.json",
                "allowed_stub_policy": "allowed only until team.build completes; forbidden by team/final gates",
            },
            conformance_tests=[f".contractcoding/interface_tests/{scope_id}.json"],
        )

    def _team_local_interface_for_scope(
        self,
        scope_id: str,
        artifacts: List[str],
        goal: str,
    ) -> Optional[InterfaceSpec]:
        symbols: List[Dict[str, Any]] = []
        for artifact in artifacts:
            artifact_symbols = self._symbols_for_artifact(artifact, scope_id, critical=False)
            if artifact_symbols:
                symbols.extend(
                    {**symbol, "artifact": artifact}
                    for symbol in artifact_symbols
                )
        if not symbols:
            for artifact in artifacts[:6]:
                symbols.extend(
                    {**symbol, "artifact": artifact}
                    for symbol in self._provided_interfaces_for_artifact(artifact, goal)
                )
        if not symbols:
            return None
        return InterfaceSpec(
            id=f"{scope_id}.team",
            owner_team=scope_id,
            consumers=[scope_id],
            artifact=artifacts[0],
            symbols=symbols[:24],
            schemas=self._schemas_for_scope(scope_id),
            semantics=self._semantics_for_scope(scope_id, critical=False),
            status="FROZEN",
            critical=False,
            source_milestone="team.interfaces",
            stability="team-local",
            conformance_tests=[f".contractcoding/interface_tests/{scope_id}.json"],
        )

    @staticmethod
    def _preferred_interface_artifact(scope_id: str, artifacts: List[str]) -> str:
        preferred_names = {
            "package": "__init__.py",
            "domain": "colony.py",
            "core": "engine.py",
              "planning": "planner.py",
              "ai": "planner.py",
            "io": "save_load.py",
            "interface": "cli.py",
        }
        preferred = preferred_names.get(scope_id, "")
        for artifact in artifacts:
            if preferred and artifact.endswith(preferred):
                return artifact
        return artifacts[0] if artifacts else ""

    @staticmethod
    def _schemas_for_scope(scope_id: str) -> List[str]:
        return {
            "domain": ["ResourceBundle", "ColonyState", "PopulationState"],
            "core": ["TurnReport", "SimulationState", "EngineEvent"],
            "planning": ["PlannerInput", "PlanRecommendation"],
            "ai": ["PlannerInput", "PlanRecommendation"],
            "io": ["SavePayload", "ScenarioPayload"],
            "interface": ["CommandResult", "CliOutput"],
            "package": ["PublicExports"],
        }.get(scope_id, [])

    @staticmethod
    def _semantics_for_scope(scope_id: str, *, critical: bool) -> List[str]:
        semantics = {
            "domain": ["state objects are deterministic and serializable", "resource and population invariants are explicit"],
            "core": ["turn advancement is deterministic", "engine reports are safe to serialize and inspect"],
            "planning": ["planner output is deterministic for the same state and policy", "recommendations are explainable"],
            "ai": ["planner output is deterministic for the same state and policy", "recommendations are explainable"],
            "io": ["save/load roundtrip preserves public state schemas", "scenario loading validates malformed payloads"],
            "interface": ["CLI/REPL commands use public package APIs", "machine-readable outputs remain stable"],
            "package": ["package exports are import-safe and documented"],
        }.get(scope_id, ["public interface remains importable and deterministic"])
        if critical:
            return [*semantics, "consumers may rely on this interface before implementation is complete"]
        return semantics

    def _symbols_for_artifact(self, artifact: str, scope_id: str, *, critical: bool) -> List[Dict[str, Any]]:
        stem = os.path.splitext(os.path.basename(artifact))[0].lower()
        if stem == "__init__":
            return [{"kind": "constant", "name": "__all__"}]
        named: Dict[tuple[str, str], List[Dict[str, Any]]] = {
            ("domain", "resources"): [
                {"kind": "class", "name": "ResourceBundle", "methods": ["to_dict() -> dict", "from_dict(data: dict) -> ResourceBundle"]},
                {"kind": "class", "name": "ResourceSpec"},
            ],
            ("domain", "population"): [
                {"kind": "class", "name": "PopulationState", "methods": ["to_dict() -> dict", "from_dict(data: dict) -> PopulationState"]},
            ],
            ("domain", "colony"): [
                {"kind": "class", "name": "ColonyState", "methods": ["to_dict() -> dict", "from_dict(data: dict) -> ColonyState"]},
            ],
            ("core", "engine"): [
                {"kind": "class", "name": "SimulationEngine", "methods": ["run_turn(state) -> TurnReport", "run(state, turns: int) -> list"]},
                {"kind": "class", "name": "TurnReport"},
            ],
            ("core", "ticks"): [
                {"kind": "class", "name": "TurnClock", "methods": ["advance() -> int"]},
                {"kind": "function", "name": "ticks_between", "signature": "ticks_between(start, end) -> int"},
            ],
            ("planning", "planner"): [
                {"kind": "class", "name": "ProjectPlanner", "methods": ["recommend(state) -> list"]},
            ],
            ("planning", "policies"): [
                {"kind": "class", "name": "PlanningPolicy"},
            ],
            ("planning", "heuristics"): [
                {"kind": "function", "name": "rank_options", "signature": "rank_options(options, context=None) -> list"},
            ],
            ("ai", "planner"): [
                {"kind": "class", "name": "ColonyPlanner", "methods": ["recommend(state) -> list"]},
            ],
            ("ai", "policies"): [
                {"kind": "class", "name": "PlanningPolicy"},
            ],
            ("io", "save_load"): [
                {"kind": "function", "name": "save_colony", "signature": "save_colony(state, path) -> None"},
                {"kind": "function", "name": "load_colony", "signature": "load_colony(path) -> object"},
            ],
            ("io", "scenarios"): [
                {"kind": "function", "name": "load_scenario", "signature": "load_scenario(name_or_path) -> object"},
            ],
            ("interface", "cli"): [
                {"kind": "function", "name": "main", "signature": "main(argv=None) -> int"},
            ],
            ("interface", "repl"): [
                {"kind": "class", "name": "InteractiveRepl"},
            ],
        }
        symbols = named.get((scope_id, stem), [])
        if symbols:
            return symbols
        return []

    def _normalize_contract(self, goal: str, contract: ContractSpec) -> ContractSpec:
        goals = contract.goals or [goal]
        scopes = self._normalize_scopes(contract.work_scopes)
        scope_ids = {scope.id for scope in scopes}
        raw_items = [
            self._normalize_item(item, scope_ids, contract.acceptance_criteria)
            for item in contract.work_items
        ]
        items, extracted_tests = self._extract_gate_test_items(raw_items)
        items = self._ensure_item_phases(items)
        scopes = self._materialize_scope_defaults(scopes, items)
        team_gates = self._materialize_team_gates(
            goals[0] if goals else goal,
            scopes,
            items,
            list(contract.team_gates),
            extracted_tests,
        )
        final_gate = self._materialize_final_gate_spec(
            goals[0] if goals else goal,
            scopes,
            items,
            team_gates,
            contract.final_gate,
        )
        metadata = dict(contract.metadata)
        metadata.setdefault("task_intent", self._task_intent(goals[0] if goals else goal))
        metadata.setdefault("delivery_type", self._delivery_type(items))
        metadata.setdefault("architecture", "phase-contract-harness-v8")
        requirements = contract.requirements
        if not isinstance(requirements, RequirementSpec) or not requirements.summary:
            requirements = self._requirements_for_contract(goals[0] if goals else goal, items, final_gate)
        architecture = contract.architecture
        if not isinstance(architecture, ArchitectureSpec) or not architecture.bounded_contexts:
            architecture = self._architecture_for_contract(scopes, items)
        milestones = list(contract.milestones) or self._default_milestones(scopes, items)
        phase_plan = list(contract.phase_plan) or self._default_phase_plan(scopes, items)
        interfaces = list(contract.interfaces) or self._interfaces_for_contract(
            goals[0] if goals else goal,
            scopes,
            items,
            team_gates,
            final_gate,
        )
        return ContractSpec(
            goals=goals,
            work_scopes=scopes,
            work_items=items,
            requirements=requirements,
            architecture=architecture,
            milestones=milestones,
            phase_plan=phase_plan,
            interfaces=interfaces,
            deltas=list(contract.deltas),
            team_gates=team_gates,
            final_gate=final_gate,
            acceptance_criteria=contract.acceptance_criteria,
            execution_policy={
                "max_parallel_teams": 4,
                "max_parallel_items_per_team": 4,
                "default_execution_plane": "sandbox",
                **contract.execution_policy,
            },
            risk_policy={"ops_default": "approval_required", **contract.risk_policy},
            verification_policy=contract.verification_policy,
            test_ownership=contract.test_ownership,
            version=CONTRACT_VERSION,
            metadata=metadata,
            owner_hints=dict(contract.owner_hints),
        )

    def _extract_gate_test_items(self, items: List[WorkItem]) -> tuple[List[WorkItem], Dict[str, List[str]]]:
        """Move generated test WorkItems into team gate ownership.

        Draft planners may describe test files as ordinary WorkItems. Runtime
        ownership is stricter: implementation teams build product artifacts,
        while team/final gates own test execution and integration review.
        """

        extracted: Dict[str, List[str]] = {}
        removed_ids: set[str] = set()
        kept: List[WorkItem] = []
        for item in items:
            test_targets = [artifact for artifact in item.target_artifacts if self._is_test_artifact(artifact)]
            non_test_targets = [artifact for artifact in item.target_artifacts if not self._is_test_artifact(artifact)]
            looks_like_test_item = (
                item.id.startswith("test:")
                or item.team_role_hint == "test_worker"
                or (test_targets and not non_test_targets)
            )
            if looks_like_test_item and test_targets:
                extracted.setdefault(item.scope_id or "tests", []).extend(test_targets)
                removed_ids.add(item.id)
                continue
            kept.append(item)

        if not removed_ids:
            return kept, extracted

        normalized: List[WorkItem] = []
        for item in kept:
            depends_on = [dependency for dependency in item.depends_on if dependency not in removed_ids]
            if depends_on == item.depends_on:
                normalized.append(item)
                continue
            payload = item.to_record()
            payload["depends_on"] = depends_on
            normalized.append(WorkItem.from_mapping(payload))
        return normalized, extracted

    @staticmethod
    def _task_intent(goal: str) -> str:
        text = " ".join(str(goal or "").split())
        return text[:400]

    @staticmethod
    def _delivery_type(items: List[WorkItem]) -> str:
        kinds = {item.kind for item in items if item.kind != "eval"}
        if not kinds:
            return "eval"
        if len(kinds) == 1:
            return next(iter(kinds))
        if kinds <= {"research", "doc"}:
            return "research"
        return "mixed"

    def _materialize_scope_defaults(self, scopes: List[WorkScope], items: List[WorkItem]) -> List[WorkScope]:
        out: List[WorkScope] = []
        for scope in scopes:
            payload = {
                "id": scope.id,
                "type": scope.type,
                "label": scope.label,
                "parent_scope": scope.parent_scope,
                "artifacts": list(scope.artifacts),
                "conflict_keys": list(scope.conflict_keys),
                "execution_plane_policy": scope.execution_plane_policy,
                "interfaces": list(scope.interfaces),
                "verification_policy": dict(scope.verification_policy),
                "test_ownership": dict(scope.test_ownership),
                "team_policy": dict(scope.team_policy),
                "promotion_policy": dict(scope.promotion_policy),
                "interface_stability": scope.interface_stability,
            }
            artifacts = [
                artifact
                for item in items
                if item.scope_id == scope.id
                for artifact in item.target_artifacts
                if not artifact.startswith(".contractcoding/")
            ]
            if artifacts and not scope.artifacts:
                payload["artifacts"] = LargeProjectPlanner._dedupe(artifacts)
            if not scope.conflict_keys and payload.get("artifacts") and scope.id != "root":
                payload["conflict_keys"] = [
                    f"scope:{scope.id}",
                    *[f"artifact:{artifact}" for artifact in payload["artifacts"]],
                ]
            if not scope.interfaces and payload.get("artifacts") and scope.type in {"code_module", "package", "tests"}:
                payload["interfaces"] = [
                    {"type": "scope_artifacts", "scope": scope.id, "artifacts": payload["artifacts"]}
                ]
            if not scope.test_ownership:
                owned_tests = [
                    artifact
                    for item in items
                    if item.scope_id == "tests"
                    for artifact in item.target_artifacts
                    if self._is_test_artifact(artifact)
                    and LargeProjectPlanner._test_focus_scope(artifact) == scope.id
                ]
                if owned_tests:
                    payload["test_ownership"] = {"owned_tests": owned_tests}
            if not scope.team_policy:
                payload["team_policy"] = self._default_team_policy(scope)
            if not scope.promotion_policy:
                payload["promotion_policy"] = self._default_promotion_policy(scope)
            out.append(WorkScope.from_mapping(payload))
        return out

    def _materialize_team_gates(
        self,
        goal: str,
        scopes: List[WorkScope],
        items: List[WorkItem],
        existing: List[TeamGateSpec],
        extracted_tests: Dict[str, List[str]],
    ) -> List[TeamGateSpec]:
        existing_by_scope = {gate.scope_id: gate for gate in existing}
        items_by_scope: Dict[str, List[WorkItem]] = {}
        for item in items:
            if item.kind == "eval":
                continue
            items_by_scope.setdefault(item.scope_id or "root", []).append(item)

        gates: List[TeamGateSpec] = []
        for scope in scopes:
            if scope.id in {"root", "integration"}:
                continue
            scope_items = items_by_scope.get(scope.id, [])
            scope_tests = LargeProjectPlanner._dedupe(
                [
                    *list(extracted_tests.get(scope.id, [])),
                    *[
                        artifact
                        for artifact in scope.artifacts
                        if self._is_test_artifact(artifact)
                    ],
                ]
            )
            if not scope_items and not scope_tests:
                continue
            existing_gate = existing_by_scope.get(scope.id)
            if existing_gate is not None:
                payload = existing_gate.to_record()
                if not payload.get("test_artifacts") and scope_tests:
                    payload["test_artifacts"] = scope_tests
                gates.append(TeamGateSpec.from_mapping(payload))
                continue
            scope_artifacts = [
                artifact
                for item in scope_items
                for artifact in item.target_artifacts
                if not artifact.startswith(".contractcoding/")
            ]
            gates.append(
                TeamGateSpec(
                    scope_id=scope.id,
                    test_artifacts=scope_tests,
                    test_plan={
                        "required_public_interfaces": scope_artifacts,
                        "test_artifacts": scope_tests,
                        "required_behaviors": LargeProjectPlanner._required_behaviors_for_scope(scope.id, goal),
                        "test_strata": "scope_local",
                        "gate_depth": "smoke",
                        "dependency_scope_ids": LargeProjectPlanner(self)._scope_dependency_scope_ids(
                            scope.id,
                            [candidate.id for candidate in scopes],
                        ),
                    },
                    deterministic_checks=LargeProjectPlanner._team_gate_checks(goal),
                )
            )
        return gates

    def _materialize_final_gate_spec(
        self,
        goal: str,
        scopes: List[WorkScope],
        items: List[WorkItem],
        team_gates: List[TeamGateSpec],
        existing: Optional[FinalGateSpec],
    ) -> FinalGateSpec:
        required = LargeProjectPlanner._dedupe(
            [
                *[
                    artifact
                    for scope in scopes
                    for artifact in scope.artifacts
                    if artifact and not artifact.startswith(".contractcoding/")
                ],
                *[
                    artifact
                    for item in items
                    if item.kind != "eval"
                    for artifact in item.target_artifacts
                    if artifact and not artifact.startswith(".contractcoding/")
                ],
                *[
                    artifact
                    for gate in team_gates
                    for artifact in gate.test_artifacts
                    if artifact and not artifact.startswith(".contractcoding/")
                ],
            ]
        )
        python_artifacts = [artifact for artifact in required if artifact.endswith(".py")]
        if existing is not None:
            payload = existing.to_record()
            payload.setdefault("required_artifacts", required)
            payload.setdefault("python_artifacts", python_artifacts)
            payload.setdefault("package_roots", LargeProjectPlanner._package_roots(python_artifacts))
            payload.setdefault("requires_tests", any(self._is_test_artifact(artifact) for artifact in required))
            payload.setdefault("final_acceptance_scenarios", LargeProjectPlanner._final_acceptance_scenarios(goal))
            payload.setdefault("product_behavior", LargeProjectPlanner._product_behavior_contract(goal, required))
            return FinalGateSpec.from_mapping(payload)
        return FinalGateSpec(
            required_artifacts=required,
            python_artifacts=python_artifacts,
            package_roots=LargeProjectPlanner._package_roots(python_artifacts),
            requires_tests=any(self._is_test_artifact(artifact) for artifact in required),
            final_acceptance_scenarios=LargeProjectPlanner._final_acceptance_scenarios(goal),
            product_behavior=LargeProjectPlanner._product_behavior_contract(goal, required),
        )

    @staticmethod
    def _default_team_policy(scope: WorkScope) -> Dict[str, Any]:
        if scope.id == "integration" or scope.type == "integration":
            return {"team_kind": "integration", "workspace_plane": "workspace"}
        if scope.type == "research":
            return {"team_kind": "research", "workspace_plane": "read-only"}
        if scope.type in {"doc"}:
            return {"team_kind": "doc", "workspace_plane": "sandbox"}
        if scope.type == "data":
            return {"team_kind": "data", "workspace_plane": "sandbox"}
        if scope.type == "ops":
            return {"team_kind": "ops", "workspace_plane": "approval-required"}
        if scope.type == "tests":
            return {"team_kind": "tests", "workspace_plane": "sandbox"}
        return {"team_kind": "coding", "workspace_plane": "worktree"}

    @staticmethod
    def _default_promotion_policy(scope: WorkScope) -> Dict[str, Any]:
        if scope.id == "integration" or scope.type in {"integration", "research", "ops"}:
            return {"mode": "none"}
        if scope.type in {"code_module", "package", "tests"}:
            return {"mode": "after_team_gate"}
        return {"mode": "artifact"}

    def _normalize_scopes(self, scopes: Iterable[WorkScope]) -> List[WorkScope]:
        normalized: List[WorkScope] = []
        seen: set[str] = set()
        for scope in scopes:
            if scope.id in seen:
                continue
            normalized.append(scope)
            seen.add(scope.id)
        if "root" not in seen:
            normalized.insert(0, default_root_scope())
        return normalized

    def _normalize_item(
        self,
        item: WorkItem,
        scope_ids: set[str],
        global_criteria: List[str],
    ) -> WorkItem:
        payload = item.to_record()
        if payload.get("scope_id") not in scope_ids:
            payload["scope_id"] = self._scope_for_item(item, scope_ids)
        if not payload.get("acceptance_criteria"):
            payload["acceptance_criteria"] = list(global_criteria)
        if not payload.get("conflict_keys"):
            payload["conflict_keys"] = self._conflict_keys_for_item(item)
        if not payload.get("execution_mode") or payload.get("execution_mode") == "auto":
            payload["execution_mode"] = self._execution_mode_for_item(item)
        if item.kind == "ops" and not payload.get("serial_group"):
            payload["serial_group"] = f"ops:{payload['scope_id']}"
        return WorkItem.from_mapping(payload)

    def _scope_for_item(self, item: WorkItem, scope_ids: set[str]) -> str:
        if item.module and item.module in scope_ids:
            return item.module
        if item.kind in scope_ids:
            return item.kind
        return "root"

    def _conflict_keys_for_item(self, item: WorkItem) -> List[str]:
        if item.target_artifacts:
            return [f"artifact:{artifact}" for artifact in item.target_artifacts]
        if item.kind == "research":
            return [f"research:{item.id}"]
        if item.kind == "ops":
            return [f"ops:{item.scope_id}"]
        return [f"item:{item.id}"]

    def _execution_mode_for_item(self, item: WorkItem) -> str:
        if item.kind == "research":
            return "read-only"
        if item.kind == "ops":
            return "approval-required"
        return "auto"
