"""Product Kernel + Feature Slice compiler.

The rewrite keeps planning deterministic and small. LLMs may draft ideas later,
but this compiler is the guardrail that freezes product semantics, slice
ownership, dependencies, and acceptance sources before workers touch files.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List

from ContractCoding.contract.spec import (
    AgentSpec,
    CanonicalSubstrate,
    ContractValidationError,
    ContractSpec,
    FeatureSlice,
    FeatureTeam,
    InterfaceCapsule,
    ProductKernel,
    QualityTransactionRecord,
    TeamSubContract,
    TeamSpec,
    TeamStateRecord,
    WorkItem,
    _dedupe,
)


CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
}


class ContractCompiler:
    """Compile a user task into the single new runtime contract shape."""

    def compile(self, goal: str) -> ContractSpec:
        artifacts = self._infer_artifacts(goal)
        tests = [artifact for artifact in artifacts if self._is_test_artifact(artifact)]
        implementation = [artifact for artifact in artifacts if artifact not in tests]
        kernel = self._build_kernel(goal, artifacts, tests)
        feature_slices = self._build_slices(implementation, tests, kernel)
        canonical_substrate = self._apply_canonical_substrate(kernel, feature_slices)
        feature_teams = self._build_feature_teams(feature_slices)
        interface_capsules = self._build_interface_capsules(feature_teams, feature_slices, canonical_substrate)
        team_subcontracts = self._build_team_subcontracts(feature_teams, feature_slices, interface_capsules, canonical_substrate)
        self._attach_team_contracts(feature_teams, interface_capsules, team_subcontracts, feature_slices)
        capsule_items = self._capsule_work_items(feature_teams)
        implementation_items = [
            WorkItem(
                id=f"slice:{feature_slice.id}",
                slice_id=feature_slice.id,
                title=f"Implement {feature_slice.title}",
                allowed_artifacts=list(feature_slice.owner_artifacts),
                dependencies=self._work_item_dependencies(feature_slice),
                kind="acceptance" if feature_slice.id == "kernel_acceptance" else "implementation",
                phase=feature_slice.phase,
                team_id=f"team:{feature_slice.feature_team_id or feature_slice.id}",
                feature_team_id=feature_slice.feature_team_id,
                conflict_keys=list(feature_slice.conflict_keys),
            )
            for feature_slice in feature_slices
            if feature_slice.owner_artifacts
        ]
        work_items = [*capsule_items, *implementation_items]
        contract = ContractSpec(
            goal=goal,
            product_kernel=kernel,
            canonical_substrate=canonical_substrate,
            feature_slices=feature_slices,
            work_items=work_items,
            required_artifacts=artifacts,
            test_artifacts=tests,
            feature_teams=feature_teams,
            team_subcontracts=team_subcontracts,
            interface_capsules=interface_capsules,
            teams=self._teams_for_feature_teams(feature_teams, team_subcontracts),
            team_states=self._initial_team_states(feature_teams, interface_capsules),
        )
        contract.validate()
        plan_quality = self._plan_quality_record(contract)
        contract.quality_transactions.append(plan_quality)
        if plan_quality.status != "APPROVED":
            raise ContractValidationError(f"plan quality rejected contract: {plan_quality.diagnostics}")
        return contract

    def _build_kernel(self, goal: str, artifacts: List[str], tests: List[str]) -> ProductKernel:
        package_roots = self._package_roots(artifacts)
        cli_modules = self._cli_modules(artifacts)
        loc_budget = self._loc_budget(goal)
        ontology = self._ontology(goal, artifacts)
        formulas = self._formulas(goal, artifacts)
        public_api_policy = self._public_api_policy(goal, artifacts)
        test_generation_policy = self._test_generation_policy()
        semantic_invariants = self._semantic_invariants(goal, artifacts, loc_budget)
        public_paths: List[Dict[str, Any]] = [
            {
                "id": "package_imports",
                "kind": "python_import",
                "package_roots": package_roots,
                "description": "All public package roots import without side effects.",
            }
        ]
        for module in cli_modules:
            public_paths.append(
                {
                    "id": f"{module}:help",
                    "kind": "cli",
                    "argv": ["{python}", "-m", module, "--help"],
                    "description": "CLI help is available and exits successfully.",
                }
            )
        acceptance = [
            {
                "id": "artifact_coverage",
                "description": "Every required artifact exists and has exactly one owner or is a locked acceptance test.",
                "invariant_refs": ["artifact_ownership"],
            },
            {
                "id": "compile_import",
                "description": "Generated Python modules compile and package roots import.",
                "invariant_refs": ["import_safe"],
            },
            {
                "id": "producer_consumer_shape",
                "description": "Feature slices expose stable public producer-consumer shapes.",
                "invariant_refs": ["producer_consumer_shape"],
            },
            {
                "id": "canonical_type_ownership",
                "description": "Kernel-owned value objects and shared enums have exactly one production owner module.",
                "invariant_refs": ["canonical_type_ownership"],
            },
            {
                "id": "slice_smoke",
                "description": "Every feature slice proves owner artifacts through import or command smoke checks before promotion.",
                "invariant_refs": ["slice_smoke_required"],
            },
            {
                "id": "controlled_mock_lifecycle",
                "description": "Temporary dependency mocks must be explicitly marked and must not remain at final integration.",
                "invariant_refs": ["no_unresolved_mocks"],
            },
            {
                "id": "semantic_kernel",
                "description": "Judges validate only frozen Product Kernel ontology, formulas, API policy, invariants, and fixtures.",
                "invariant_refs": [invariant["id"] for invariant in semantic_invariants],
            },
            {
                "id": "kernel_derived_acceptance",
                "description": "Final tests are compiled from Product Kernel acceptance rows and cannot introduce ungrounded product rules.",
                "invariant_refs": ["tests_compile_kernel_acceptance"],
            },
            {
                "id": "public_behavior_flow",
                "description": "Declared public behavior probes execute through public producer-consumer APIs.",
                "invariant_refs": ["public_behavior_examples"],
            },
        ]
        if tests:
            acceptance.append(
                {
                    "id": "declared_tests_pass",
                    "description": "Declared tests execute against public behavior and do not invent product semantics.",
                    "invariant_refs": ["tests_compile_kernel_acceptance"],
                }
            )
        return ProductKernel(
            ontology=ontology,
            formulas=formulas,
            public_api_policy=public_api_policy,
            test_generation_policy=test_generation_policy,
            schemas=[
                {"id": "artifact_manifest", "artifacts": artifacts},
                {"id": "public_runtime_contract", "package_roots": package_roots},
                {
                    "id": "slice_interface_contract",
                    "description": "Each slice declares public modules, consumers, smoke checks, and soft quality signals before workers run.",
                },
            ],
            fixtures=[
                {
                    "id": "smoke_workspace",
                    "required_artifact_count": len(artifacts),
                    "goal_excerpt": goal[:240],
                    "loc_budget": loc_budget,
                }
            ],
            flows=[
                {
                    "id": "slice_to_integration",
                    "description": "Workers build bounded feature slices; judges validate frozen kernel acceptance.",
                },
                {
                    "id": "repair_transaction_validation",
                    "description": "Final repair runs in an isolated workspace and promotes only after locked exact validation passes.",
                }
            ]
            + self._public_behavior_flows(artifacts),
            invariants=[
                {"id": "artifact_ownership", "description": "Each implementation artifact has one owner slice."},
                {"id": "import_safe", "description": "Imports have no prompts, daemon loops, or network side effects."},
                {"id": "producer_consumer_shape", "description": "Downstream slices use public producer APIs."},
                {"id": "canonical_type_ownership", "description": "Shared value objects and enums are defined once and imported by consumers."},
                {"id": "ontology_consistency", "description": "Value objects and coordinate systems are not implicitly equated across slices."},
                {"id": "formulas_are_sources", "description": "Exact numeric expectations require a declared formula or fixture source."},
                {"id": "public_api_policy_source", "description": "Package export assertions follow the kernel public_api_policy."},
                {"id": "slice_smoke_required", "description": "Slice promotion requires executable smoke evidence for owned public modules."},
                {"id": "no_placeholder_behavior", "description": "No TODO, pass-only, or NotImplemented behavior remains."},
                {"id": "no_unresolved_mocks", "description": "Marked CONTRACTCODING_MOCK sections are forbidden in final integration."},
                {
                    "id": "tests_compile_kernel_acceptance",
                    "description": "Tests compile frozen Product Kernel acceptance, not fresh product rules.",
                },
            ],
            semantic_invariants=semantic_invariants,
            acceptance_matrix=acceptance,
            public_paths=public_paths,
        )

    def _build_slices(
        self,
        implementation_artifacts: List[str],
        test_artifacts: List[str],
        kernel: ProductKernel,
    ) -> List[FeatureSlice]:
        grouped: Dict[str, List[str]] = {}
        granular = self._uses_granular_slices(implementation_artifacts)
        slice_key_by_artifact: Dict[str, str] = {}
        for artifact in implementation_artifacts:
            key = self._granular_slice_key(artifact) if granular else self._slice_key(artifact)
            slice_key_by_artifact[artifact] = key
            grouped.setdefault(key, []).append(artifact)
        slice_budgets = self._slice_loc_budgets(kernel, grouped, test_artifacts)
        order = self._slice_order(grouped, granular=granular)
        slices: List[FeatureSlice] = []
        for key in [*order, *sorted(k for k in grouped if k not in order)]:
            owners = grouped.get(key, [])
            if not owners:
                continue
            dependencies = self._dependencies_for_key(key, grouped, granular=granular)
            consumers = [
                artifact
                for artifact in implementation_artifacts
                if artifact not in owners and key in self._dependencies_for_key(slice_key_by_artifact[artifact], grouped, granular=granular)
            ]
            interface_contract = self._interface_contract_for(
                key=key,
                owners=owners,
                consumers=consumers,
                dependencies=dependencies,
                size_budget=slice_budgets.get(key, {}),
            )
            semantic_contract = self._semantic_contract_for(key, owners, consumers, dependencies, kernel)
            slices.append(
                FeatureSlice(
                    id=key,
                    title=self._slice_title(key),
                    owner_artifacts=owners,
                    consumer_artifacts=list(interface_contract["consumer_artifacts"]),
                    dependencies=dependencies,
                    fixture_refs=["smoke_workspace"],
                    invariant_refs=self._invariants_for(key),
                    acceptance_refs=["artifact_coverage", "compile_import", "producer_consumer_shape", "slice_smoke"],
                    done_contract=[
                        "All owner artifacts exist.",
                        "All Python owner artifacts compile.",
                        "Owned public behavior follows the slice semantic_contract and kernel ontology.",
                        "Public producer symbols needed by dependent slices are present.",
                        "Slice smoke and consumer contract checks pass before promotion.",
                        "Scale targets are reported as quality signals, not slice promotion blockers.",
                        "No placeholder implementation remains.",
                    ],
                    interface_contract=interface_contract,
                    semantic_contract=semantic_contract,
                    slice_smoke=self._slice_smoke_for(key, owners),
                    conflict_keys=[f"artifact:{artifact}" for artifact in owners],
                )
            )
        if test_artifacts:
            interface_contract = self._interface_contract_for(
                key="kernel_acceptance",
                owners=test_artifacts,
                consumers=[],
                dependencies=[feature_slice.id for feature_slice in slices],
                size_budget=slice_budgets.get("kernel_acceptance", {}),
            )
            semantic_contract = self._semantic_contract_for(
                "kernel_acceptance",
                test_artifacts,
                [],
                [feature_slice.id for feature_slice in slices],
                kernel,
            )
            slices.append(
                FeatureSlice(
                    id="kernel_acceptance",
                    title="Kernel acceptance tests",
                    owner_artifacts=test_artifacts,
                    dependencies=[feature_slice.id for feature_slice in slices],
                    fixture_refs=["smoke_workspace"],
                    invariant_refs=["tests_compile_kernel_acceptance"],
                    acceptance_refs=["declared_tests_pass", "semantic_kernel"],
                    done_contract=[
                        "Tests are generated from Product Kernel acceptance rows and declared slice contracts.",
                        "Tests do not assert exact numeric values, rankings, or package export policies unless sourced from kernel fixtures/formulas/API policy.",
                        "Tests remain locked during final repair.",
                    ],
                    interface_contract=interface_contract,
                    semantic_contract=semantic_contract,
                    slice_smoke=[],
                    phase="slice.acceptance",
                    conflict_keys=[f"artifact:{artifact}" for artifact in test_artifacts],
                )
            )
        return slices

    def _apply_canonical_substrate(self, kernel: ProductKernel, feature_slices: List[FeatureSlice]) -> CanonicalSubstrate:
        """Make shared type ownership an early executable phase.

        This is the key planner change from module buckets to product semantic
        coordination: canonical types are not merely final-gate lint. Their
        owner slice becomes a substrate slice, and every consumer waits on it.
        """

        owners = dict((kernel.ontology or {}).get("canonical_type_owners", {}) or {})
        if not owners:
            return CanonicalSubstrate(status="NOT_REQUIRED")
        artifact_to_slice: Dict[str, FeatureSlice] = {}
        for feature_slice in feature_slices:
            for artifact in feature_slice.owner_artifacts:
                artifact_to_slice[artifact] = feature_slice
        owner_artifacts = _dedupe(artifact for artifact in owners.values() if artifact in artifact_to_slice)
        substrate_slice_ids = _dedupe(artifact_to_slice[artifact].id for artifact in owner_artifacts)
        if not substrate_slice_ids:
            return CanonicalSubstrate(
                owner_by_type=owners,
                owner_artifacts=owner_artifacts,
                status="MISSING_OWNER_SLICE",
            )
        substrate_by_slice: Dict[str, Dict[str, str]] = {}
        for type_name, artifact in owners.items():
            owner_slice = artifact_to_slice.get(artifact)
            if owner_slice is None:
                continue
            substrate_by_slice.setdefault(owner_slice.id, {})[type_name] = artifact
        for feature_slice in feature_slices:
            owned_types = substrate_by_slice.get(feature_slice.id, {})
            if not owned_types:
                continue
            feature_slice.phase = "kernel.substrate"
            feature_slice.feature_team_id = "canonical_substrate"
            feature_slice.invariant_refs = _dedupe([*feature_slice.invariant_refs, "canonical_type_ownership"])
            feature_slice.acceptance_refs = _dedupe([*feature_slice.acceptance_refs, "canonical_type_ownership"])
            feature_slice.done_contract = _dedupe(
                [
                    "Define each Product Kernel canonical value object/enum exactly once in its owner artifact.",
                    "Export stable constructors, serialization helpers, and equality semantics for consumers.",
                    "Do not import consumer modules from the substrate owner.",
                    *feature_slice.done_contract,
                ]
            )
            feature_slice.interface_contract["canonical_exports"] = dict(owned_types)
            feature_slice.interface_contract["canonical_imports"] = {
                type_name: artifact for type_name, artifact in owners.items() if type_name not in owned_types
            }
            feature_slice.semantic_contract["canonical_type_owners"] = dict(owners)
            feature_slice.semantic_contract["owned_canonical_types"] = dict(owned_types)
            feature_slice.conflict_keys = _dedupe(
                [*feature_slice.conflict_keys, *[f"canonical:{type_name}" for type_name in owned_types]]
            )
        value_object_names = set(dict((kernel.ontology or {}).get("value_objects", {}) or {}))
        base_substrate_slice_ids = _dedupe(
            artifact_to_slice[owners[type_name]].id
            for type_name in value_object_names
            if type_name in owners and owners[type_name] in artifact_to_slice
        )
        for feature_slice in feature_slices:
            if feature_slice.id not in substrate_slice_ids or feature_slice.id in base_substrate_slice_ids:
                continue
            feature_slice.dependencies = _dedupe(
                [*feature_slice.dependencies, *[slice_id for slice_id in base_substrate_slice_ids if slice_id != feature_slice.id]]
            )
        consumer_slice_ids: List[str] = []
        for feature_slice in feature_slices:
            if feature_slice.id in substrate_slice_ids or feature_slice.id == "package_surface":
                continue
            feature_slice.dependencies = _dedupe([*feature_slice.dependencies, *substrate_slice_ids])
            feature_slice.interface_contract["canonical_imports"] = dict(owners)
            feature_slice.semantic_contract["canonical_type_owners"] = dict(owners)
            if any(artifact.endswith(".py") for artifact in feature_slice.owner_artifacts):
                consumer_slice_ids.append(feature_slice.id)
        kernel.ontology["canonical_substrate"] = {
            "owner_by_type": dict(owners),
            "owner_artifacts": list(owner_artifacts),
            "substrate_slice_ids": list(substrate_slice_ids),
            "consumer_slice_ids": _dedupe(consumer_slice_ids),
        }
        return CanonicalSubstrate(
            owner_by_type=owners,
            owner_artifacts=owner_artifacts,
            substrate_slice_ids=substrate_slice_ids,
            consumer_slice_ids=_dedupe(consumer_slice_ids),
            status="PLANNED",
        )

    def _build_feature_teams(self, feature_slices: List[FeatureSlice]) -> List[FeatureTeam]:
        """Group executable slices into coarse feature teams.

        Team boundaries are product/interface boundaries, not directory
        buckets. A team can own multiple non-conflicting slices and expose one
        locked interface capsule for downstream teams.
        """

        by_slice: Dict[str, FeatureSlice] = {feature_slice.id: feature_slice for feature_slice in feature_slices}
        grouped: Dict[str, List[FeatureSlice]] = {}
        for feature_slice in feature_slices:
            team_id = feature_slice.feature_team_id or self._feature_team_id_for_slice(feature_slice.id)
            feature_slice.feature_team_id = team_id
            grouped.setdefault(team_id, []).append(feature_slice)

        order = [
            "foundation",
            "canonical_substrate",
            "domain_kernel",
            "core_engine",
            "planning_intelligence",
            "scenario_persistence",
            "public_interface",
            "app_integration",
            "kernel_acceptance",
        ]
        teams: Dict[str, FeatureTeam] = {}
        for team_id in [*order, *sorted(key for key in grouped if key not in order)]:
            slices = grouped.get(team_id, [])
            if not slices:
                continue
            owner_artifacts: List[str] = []
            dependencies: List[str] = []
            acceptance_refs: List[str] = []
            for feature_slice in slices:
                owner_artifacts.extend(feature_slice.owner_artifacts)
                acceptance_refs.extend(feature_slice.acceptance_refs)
                for dependency_id in feature_slice.dependencies:
                    dependency = by_slice.get(dependency_id)
                    if dependency is None:
                        continue
                    dependency_team = dependency.feature_team_id or self._feature_team_id_for_slice(dependency.id)
                    if dependency_team != team_id:
                        dependencies.append(dependency_team)
            teams[team_id] = FeatureTeam(
                id=team_id,
                title=self._feature_team_title(team_id),
                slice_ids=[feature_slice.id for feature_slice in slices],
                owner_artifacts=_dedupe(owner_artifacts),
                dependencies=_dedupe(dependencies),
                acceptance_refs=_dedupe(acceptance_refs),
                interface_capsule_refs=[],
                local_done_contract=self._team_done_contract(team_id),
                team_contract=self._team_contract(team_id, slices, _dedupe(dependencies), _dedupe(acceptance_refs)),
                coordination_mode="mixed" if len(slices) > 1 else "single_slice",
            )

        for team in teams.values():
            consumers = [
                candidate.id
                for candidate in teams.values()
                if team.id in candidate.dependencies
            ]
            team.consumer_team_ids = _dedupe(consumers)
        return list(teams.values())

    def _build_interface_capsules(
        self,
        feature_teams: List[FeatureTeam],
        feature_slices: List[FeatureSlice],
        canonical_substrate: CanonicalSubstrate,
    ) -> List[InterfaceCapsule]:
        slices_by_team: Dict[str, List[FeatureSlice]] = {}
        for feature_slice in feature_slices:
            slices_by_team.setdefault(feature_slice.feature_team_id, []).append(feature_slice)
        capsules: List[InterfaceCapsule] = []
        substrate_imports = dict(canonical_substrate.owner_by_type or {})
        for team in feature_teams:
            slices = slices_by_team.get(team.id, [])
            public_modules = self._python_modules(team.owner_artifacts, include_tests=False)
            smoke: List[Dict[str, Any]] = []
            required_shapes: List[Dict[str, Any]] = []
            contract_tests: List[Dict[str, Any]] = []
            for feature_slice in slices:
                smoke.extend(feature_slice.slice_smoke)
                required_shapes.extend(feature_slice.interface_contract.get("required_shapes", []) or [])
                for flow in feature_slice.slice_smoke:
                    contract_tests.append({"id": flow.get("id"), "kind": flow.get("kind"), "source": "slice_smoke"})
            examples: List[Dict[str, Any]] = []
            if public_modules:
                imports = "\n".join(f"import {module}" for module in public_modules[:8])
                examples.append(
                    {
                        "id": f"{team.id}:import_public_modules",
                        "kind": "python_import_example",
                        "modules": public_modules[:8],
                        "code": imports,
                    }
                )
            examples.append(
                {
                    "id": f"{team.id}:producer_consumer_rule",
                    "kind": "interface_rule",
                    "rule": "Consumers must inspect this team's public modules and examples before constructing objects or calling helpers.",
                }
            )
            if team.id in {"domain_kernel", "core_engine", "planning_intelligence", "scenario_persistence"}:
                examples.append(
                    {
                        "id": f"{team.id}:serialization_policy",
                        "kind": "shape_policy",
                        "rule": "Prefer exported constructors, dataclasses, enums, and from_dict/to_dict helpers over raw private fields.",
                    }
                )
            owned_canonical = {
                type_name: artifact
                for type_name, artifact in substrate_imports.items()
                if artifact in team.owner_artifacts
            }
            imports_for_team = {
                type_name: artifact
                for type_name, artifact in substrate_imports.items()
                if type_name not in owned_canonical
            }
            capsules.append(
                InterfaceCapsule(
                    id=f"capsule:{team.id}",
                    team_id=team.id,
                    version="v1",
                    producer_slice_ids=list(team.slice_ids),
                    consumer_team_ids=list(team.consumer_team_ids),
                    owner_artifacts=list(team.owner_artifacts),
                    public_modules=public_modules,
                    canonical_imports=imports_for_team,
                    capabilities=self._interface_capabilities(team.id, required_shapes, public_modules),
                    key_signatures=required_shapes[:24],
                    examples=examples,
                    fixtures=[
                        {
                            "id": f"{team.id}:owner_manifest",
                            "owner_artifacts": list(team.owner_artifacts),
                            "required_shapes": required_shapes[:24],
                            "owned_canonical_types": owned_canonical,
                            "imported_canonical_types": imports_for_team,
                        }
                    ],
                    smoke=smoke,
                    contract_tests=contract_tests[:24],
                    lock_item_id=f"capsule:{team.id}:lock",
                    compatibility={
                        "breaking_change_requires": "affected-slice replan",
                        "consumers_must_inspect": ["public_modules", "canonical_imports", "key_signatures", "examples"],
                    },
                    status="INTENT",
                )
            )
        return capsules

    def _build_team_subcontracts(
        self,
        feature_teams: List[FeatureTeam],
        feature_slices: List[FeatureSlice],
        interface_capsules: List[InterfaceCapsule],
        canonical_substrate: CanonicalSubstrate,
    ) -> List[TeamSubContract]:
        slices_by_team: Dict[str, List[FeatureSlice]] = {}
        for feature_slice in feature_slices:
            slices_by_team.setdefault(feature_slice.feature_team_id, []).append(feature_slice)
        capsule_by_team = {capsule.team_id: capsule for capsule in interface_capsules}
        subcontracts: List[TeamSubContract] = []
        for team in feature_teams:
            slices = slices_by_team.get(team.id, [])
            slice_ids = [feature_slice.id for feature_slice in slices]
            serial_edges = [
                {"after": dependency, "before": feature_slice.id}
                for feature_slice in slices
                for dependency in feature_slice.dependencies
                if dependency in slice_ids
            ]
            parallel = [
                [
                    feature_slice.id
                    for feature_slice in slices
                    if not any(dependency in slice_ids for dependency in feature_slice.dependencies)
                ]
            ]
            parallel = [group for group in parallel if len(group) > 1]
            owned_concepts = [
                type_name
                for type_name, artifact in (canonical_substrate.owner_by_type or {}).items()
                if artifact in team.owner_artifacts
            ]
            capsule = capsule_by_team.get(team.id)
            dependency_capsules = [f"capsule:{dependency}" for dependency in team.dependencies]
            subcontracts.append(
                TeamSubContract(
                    id=f"subcontract:{team.id}",
                    team_id=team.id,
                    purpose=self._feature_team_title(team.id),
                    slice_ids=slice_ids,
                    owner_artifacts=list(team.owner_artifacts),
                    owned_concepts=owned_concepts,
                    dependency_team_ids=list(team.dependencies),
                    dependency_capsule_refs=dependency_capsules,
                    interface_capsule_refs=[capsule.id] if capsule is not None else [],
                    local_done_contract=self._team_done_contract(team.id),
                    local_quality_gates=[
                        {"id": "scope", "kind": "artifact_scope", "allowed_artifacts": list(team.owner_artifacts)},
                        {"id": "compile_import", "kind": "compile_import"},
                        {"id": "capsule_shape", "kind": "interface_capsule"},
                        {"id": "canonical_ownership", "kind": "canonical_type_ownership"},
                    ],
                    internal_parallel_groups=parallel,
                    internal_serial_edges=serial_edges,
                    agent_roles=[
                        {"role": "team_lead", "contract": "maintain subcontract and choose ready slice"},
                        {"role": "slice_worker_pool", "contract": "edit only current allowed artifacts"},
                        {"role": "interface_steward", "contract": "keep capsule examples and shapes stable"},
                        {"role": "quality_reviewer", "contract": "pair review and tests before submit_result"},
                    ],
                    context_policy={
                        "default": "subcontract + current slice + direct dependency capsules",
                        "forbidden": ["full unrelated slice graph", "private downstream implementation guesses"],
                        "required_tools": ["contract_snapshot", "inspect_module_api"],
                    },
                    escalation_policy={
                        "report_blocker_when": [
                            "needed artifact is outside allowed_artifacts",
                            "dependency capsule contradicts inspected API",
                            "canonical owner is missing or ambiguous",
                        ],
                        "replan_when": ["capsule compatibility break", "repeated semantic fingerprint"],
                    },
                )
            )
        return subcontracts

    def _attach_team_contracts(
        self,
        feature_teams: List[FeatureTeam],
        interface_capsules: List[InterfaceCapsule],
        team_subcontracts: List[TeamSubContract],
        feature_slices: List[FeatureSlice],
    ) -> None:
        capsule_by_team = {capsule.team_id: capsule for capsule in interface_capsules}
        subcontract_by_team = {subcontract.team_id: subcontract for subcontract in team_subcontracts}
        for team in feature_teams:
            capsule = capsule_by_team.get(team.id)
            if capsule is not None:
                team.interface_capsule_refs = [capsule.id]
            subcontract = subcontract_by_team.get(team.id)
            if subcontract is not None:
                team.subcontract_ref = subcontract.id
                team.team_contract = subcontract.to_record()
        for feature_slice in feature_slices:
            capsule = capsule_by_team.get(feature_slice.feature_team_id)
            feature_slice.interface_contract["feature_team_id"] = feature_slice.feature_team_id
            if capsule is not None:
                feature_slice.interface_contract["interface_capsule_ref"] = capsule.id
                feature_slice.interface_contract["canonical_imports"] = dict(capsule.canonical_imports)

    @staticmethod
    def _capsule_work_items(feature_teams: List[FeatureTeam]) -> List[WorkItem]:
        items: List[WorkItem] = []
        for team in feature_teams:
            if team.id == "kernel_acceptance" or not team.owner_artifacts:
                continue
            items.append(
                WorkItem(
                    id=f"capsule:{team.id}:lock",
                    slice_id=f"capsule:{team.id}",
                    title=f"Lock interface capsule for {team.title}",
                    allowed_artifacts=[],
                    dependencies=[],
                    kind="capsule",
                    phase="team.capsule",
                    team_id=f"team:{team.id}",
                    feature_team_id=team.id,
                    conflict_keys=[f"capsule:{team.id}"],
                )
            )
        return items

    @staticmethod
    def _work_item_dependencies(feature_slice: FeatureSlice) -> List[str]:
        dependencies: List[str] = []
        if feature_slice.feature_team_id and feature_slice.feature_team_id != "kernel_acceptance":
            dependencies.append(f"capsule:{feature_slice.feature_team_id}")
        for dependency in feature_slice.dependencies:
            dependencies.append(dependency)
        return _dedupe(dependencies)

    @staticmethod
    def _initial_team_states(
        feature_teams: List[FeatureTeam],
        interface_capsules: List[InterfaceCapsule],
    ) -> List[TeamStateRecord]:
        capsules_by_team = {capsule.team_id: capsule for capsule in interface_capsules}
        states: List[TeamStateRecord] = []
        for team in feature_teams:
            capsule = capsules_by_team.get(team.id)
            capsule_refs = [capsule.id] if capsule is not None else []
            states.append(
                TeamStateRecord(
                    team_id=team.id,
                    phase="capsule" if capsule_refs and team.id != "kernel_acceptance" else "waiting",
                    interface_refs=capsule_refs,
                    frozen_interfaces=[],
                    waiting_on_interfaces=[f"capsule:{dependency}" for dependency in team.dependencies],
                    mailbox=[
                        {
                            "type": "capsule_request",
                            "from_team_id": consumer,
                            "to_team_id": team.id,
                            "interface_ref": capsule.id if capsule is not None else "",
                            "status": "OPEN",
                        }
                        for consumer in team.consumer_team_ids
                    ],
                )
            )
        return states

    def _plan_quality_record(self, contract: ContractSpec) -> QualityTransactionRecord:
        diagnostics: List[Dict[str, Any]] = []
        capsule_items = {item.slice_id for item in contract.work_items if item.kind == "capsule"}
        for team in contract.feature_teams:
            if team.id == "kernel_acceptance":
                continue
            expected = f"capsule:{team.id}"
            if expected not in capsule_items:
                diagnostics.append(
                    {
                        "code": "plan_missing_capsule_lock_item",
                        "artifact": team.id,
                        "message": f"{team.id} has no interface capsule lock work item",
                        "kernel_invariant": "producer_consumer_shape",
                    }
                )
            if not team.subcontract_ref:
                diagnostics.append(
                    {
                        "code": "plan_missing_team_subcontract",
                        "artifact": team.id,
                        "message": f"{team.id} has no team subcontract",
                        "kernel_invariant": "producer_consumer_shape",
                    }
                )
        for item in contract.work_items:
            if item.kind != "implementation":
                continue
            expected = f"capsule:{item.feature_team_id}"
            if expected not in item.dependencies:
                diagnostics.append(
                    {
                        "code": "plan_slice_missing_own_capsule_dependency",
                        "artifact": item.id,
                        "message": f"{item.id} does not wait for {expected}",
                        "kernel_invariant": "producer_consumer_shape",
                    }
                )
        for capsule in contract.interface_capsules:
            if capsule.team_id != "kernel_acceptance" and not capsule.examples:
                diagnostics.append(
                    {
                        "code": "plan_capsule_missing_examples",
                        "artifact": capsule.id,
                        "message": f"{capsule.id} has no executable examples",
                        "kernel_invariant": "producer_consumer_shape",
                    }
                )
        if contract.canonical_substrate.owner_by_type:
            if not contract.canonical_substrate.substrate_slice_ids:
                diagnostics.append(
                    {
                        "code": "plan_missing_canonical_substrate_slice",
                        "artifact": "canonical_substrate",
                        "message": "canonical types have no substrate owner slice",
                        "kernel_invariant": "canonical_type_ownership",
                    }
                )
            for feature_slice in contract.feature_slices:
                if feature_slice.id in contract.canonical_substrate.consumer_slice_ids:
                    missing = [
                        slice_id
                        for slice_id in contract.canonical_substrate.substrate_slice_ids
                        if slice_id not in feature_slice.dependencies
                    ]
                    if missing:
                        diagnostics.append(
                            {
                                "code": "plan_slice_missing_canonical_substrate_dependency",
                                "artifact": feature_slice.id,
                                "message": f"{feature_slice.id} does not wait for canonical substrate slices {missing}",
                                "kernel_invariant": "canonical_type_ownership",
                            }
                        )
        verdict = "APPROVE" if not diagnostics else "REQUEST_CHANGES"
        return QualityTransactionRecord(
            id="quality:plan:compile:1",
            run_id="compile",
            scope="plan",
            item_id="contract.compile",
            slice_id="plan",
            verdict=verdict,
            test_evidence=["plan_quality:team_graph_checked", "plan_quality:interface_capsule_items_checked", "plan_quality:canonical_substrate_checked"],
            review_evidence=["plan_review:boundary_contract_only", f"plan_review:{verdict.lower()}"],
            diagnostics=diagnostics,
            status="APPROVED" if not diagnostics else "REJECTED",
        )

    def _teams_for_feature_teams(
        self,
        feature_teams: List[FeatureTeam],
        team_subcontracts: List[TeamSubContract],
    ) -> List[TeamSpec]:
        teams: List[TeamSpec] = []
        subcontract_by_team = {subcontract.team_id: subcontract for subcontract in team_subcontracts}
        for feature_team in feature_teams:
            if not feature_team.owner_artifacts:
                continue
            subcontract = subcontract_by_team.get(feature_team.id)
            skills = (
                ["acceptance_test_authoring", "dependency_interface_consumption", "code_test_slice", "judge_contract_verification"]
                if feature_team.id == "kernel_acceptance"
                else [
                    "feature_slice_design",
                    "dependency_interface_consumption",
                    "interface_contract_authoring",
                    "code_generation_slice",
                    "code_test_slice",
                    "tool_use_protocol",
                    "evidence_submission_protocol",
                ]
            )
            teams.append(
                TeamSpec(
                    id=f"team:{feature_team.id}",
                    slice_id=feature_team.slice_ids[0] if feature_team.slice_ids else feature_team.id,
                    feature_team_id=feature_team.id,
                    slice_ids=list(feature_team.slice_ids),
                    local_contract=subcontract.to_record() if subcontract is not None else dict(feature_team.team_contract),
                    phase="slice.acceptance" if feature_team.id == "kernel_acceptance" else "slice.build",
                    coordination_mode=feature_team.coordination_mode,
                    agents=[
                        AgentSpec(
                            id=f"{feature_team.id}:lead",
                            role="team_lead",
                            skills=[
                                "managed_feature_team_coordination",
                                "feature_slice_design",
                                "interface_contract_authoring",
                                "interface_capsule_handshake",
                                "code_test_slice",
                                "judge_contract_verification",
                                "evidence_submission_protocol",
                            ],
                            owns=[],
                        ),
                        AgentSpec(
                            id=f"{feature_team.id}:implementer",
                            role="slice_worker_pool",
                            skills=skills,
                            owns=list(feature_team.owner_artifacts),
                        ),
                        AgentSpec(
                            id=f"{feature_team.id}:interface_steward",
                            role="interface_steward",
                            skills=["dependency_interface_consumption", "interface_contract_authoring", "interface_capsule_handshake", "judge_contract_verification"],
                            owns=[],
                        ),
                        AgentSpec(
                            id=f"{feature_team.id}:reviewer",
                            role="team_reviewer",
                            skills=["code_test_slice", "judge_contract_verification", "evidence_submission_protocol"],
                            owns=list(feature_team.owner_artifacts),
                        ),
                    ],
                )
            )
        return teams

    @staticmethod
    def _team_contract(
        team_id: str,
        slices: List[FeatureSlice],
        dependency_team_ids: List[str],
        acceptance_refs: List[str],
    ) -> Dict[str, Any]:
        slice_ids = [feature_slice.id for feature_slice in slices]
        parallel_candidates = [
            feature_slice.id
            for feature_slice in slices
            if not any(dependency in slice_ids for dependency in feature_slice.dependencies)
        ]
        serial_edges = [
            {"after": dependency, "before": feature_slice.id}
            for feature_slice in slices
            for dependency in feature_slice.dependencies
            if dependency in slice_ids
        ]
        return {
            "id": f"team_contract:{team_id}",
            "source": "product_kernel_feature_slice_graph",
            "slice_ids": slice_ids,
            "dependency_team_ids": list(dependency_team_ids),
            "acceptance_refs": list(acceptance_refs),
            "internal_parallel_candidates": parallel_candidates,
            "internal_serial_edges": serial_edges,
            "agent_team_flow": [
                {
                    "role": "team_lead",
                    "responsibility": "read the feature-team contract, choose the next ready slice, and keep dependency evidence visible",
                },
                {
                    "role": "slice_worker_pool",
                    "responsibility": "implement only current allowed_artifacts, using dependency interface capsules",
                },
                {
                    "role": "interface_steward",
                    "responsibility": "freeze public modules, examples, and producer-consumer shapes for downstream teams",
                },
                {
                    "role": "team_reviewer",
                    "responsibility": "run or cite slice smoke, compile/import checks, and concrete evidence before submit_result",
                },
            ],
            "mock_policy": {
                "allowed": "Only for unavailable downstream dependencies named in dependency interfaces.",
                "metadata_required": ["mock_id", "real_owner_slice", "allowed_until_phase", "contract_tests"],
                "final_gate_must_fail_on_unresolved_mocks": True,
            },
        }

    @staticmethod
    def _feature_team_id_for_slice(slice_id: str) -> str:
        if slice_id == "kernel_acceptance":
            return "kernel_acceptance"
        if slice_id == "package_surface":
            return "foundation"
        if slice_id in {"domain_foundation"} or slice_id.startswith(("domain_", "utility_")):
            return "domain_kernel"
        if slice_id in {"behavior_engine"} or slice_id.startswith("core_"):
            return "core_engine"
        if slice_id in {"planning_intelligence"} or slice_id.startswith("planning_"):
            return "planning_intelligence"
        if slice_id in {"persistence_flow"} or slice_id.startswith("io_"):
            return "scenario_persistence"
        if slice_id in {"public_interface"} or slice_id.startswith("interface_"):
            return "public_interface"
        return "app_integration"

    @staticmethod
    def _feature_team_title(team_id: str) -> str:
        return {
            "foundation": "package surface and exports team",
            "canonical_substrate": "canonical substrate team",
            "domain_kernel": "domain kernel team",
            "core_engine": "core engine team",
            "planning_intelligence": "planning intelligence team",
            "scenario_persistence": "scenario and persistence team",
            "public_interface": "public interface team",
            "app_integration": "application integration team",
            "kernel_acceptance": "kernel acceptance team",
        }.get(team_id, f"{team_id.replace('_', ' ')} team")

    @staticmethod
    def _team_done_contract(team_id: str) -> List[str]:
        base = [
            "Own and validate the team's allowed artifacts only.",
            "Expose one locked interface capsule for downstream teams.",
            "Use upstream interface capsules rather than guessing private shapes.",
            "Pass team-local compile/import/smoke checks before promotion.",
            "Submit concrete changed files and evidence, not narrative completion.",
        ]
        if team_id == "kernel_acceptance":
            return [
                "Compile final tests from Product Kernel acceptance and locked interface capsules.",
                "Keep acceptance tests as locked artifacts after promotion.",
                "Do not invent new product semantics during final validation.",
            ]
        if team_id == "foundation":
            base.append("Keep package exports import-safe and side-effect free.")
        if team_id == "canonical_substrate":
            base.append("Define shared canonical value objects/enums once and export adapters for every consumer.")
        if team_id == "domain_kernel":
            base.append("Publish canonical schemas, constructors, enum policies, and serialization helpers.")
        if team_id == "public_interface":
            base.append("Wire public CLI/API flows through upstream public interfaces only.")
        return base

    def _infer_artifacts(self, goal: str) -> List[str]:
        explicit = self._extract_artifact_paths(goal)
        if explicit:
            return explicit
        package = self._infer_package_name(goal)
        return [
            f"{package}/__init__.py",
            f"{package}/domain/models.py",
            f"{package}/core/engine.py",
            f"{package}/io/storage.py",
            f"{package}/interface/cli.py",
            "tests/test_integration.py",
        ]

    @staticmethod
    def _extract_artifact_paths(goal: str) -> List[str]:
        candidates = re.findall(r"(?<![\w/.-])([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)", goal)
        out: List[str] = []
        for candidate in candidates:
            path = candidate.strip().strip("`'\".,;:()[]{}")
            ext = os.path.splitext(path)[1].lower()
            if ext in CODE_EXTENSIONS and not path.startswith(("http://", "https://")):
                out.append(os.path.normpath(path).replace("\\", "/"))
        return _dedupe(out)

    @staticmethod
    def _infer_package_name(goal: str) -> str:
        match = re.search(r"(?:named|package|called|名为)\s+([A-Za-z_][A-Za-z0-9_]*)", goal)
        if match:
            return match.group(1)
        return "generated_app"

    @staticmethod
    def _loc_budget(goal: str) -> Dict[str, Any]:
        lowered = goal.lower()
        patterns = [
            r"(?:at\s+least|minimum|>=|不少于|至少)\s*(\d{4,6})\s*(?:meaningful\s+)?(?:non-empty\s+)?(?:loc|lines|行)",
            r"(\d{4,6})\s*(?:meaningful\s+)?(?:non-empty\s+)?(?:loc|lines|行)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return {
                    "enabled": True,
                    "min_total_loc": int(match.group(1)),
                    "line_policy": "non_empty_non_comment",
                    "no_padding": True,
                    "source": "explicit_goal",
                }
        return {"enabled": False}

    @staticmethod
    def _ontology(goal: str, artifacts: List[str]) -> Dict[str, Any]:
        text = " ".join([goal, *artifacts]).lower()
        value_objects: Dict[str, Any] = {}
        forbidden_equivalences: List[Dict[str, Any]] = []
        conversion_policies: List[Dict[str, Any]] = []
        if any(word in text for word in ["geo", "coordinate", "coordinates", "routing", "route", "distance", "map", "grid"]):
            value_objects["GeoPoint"] = {
                "kind": "value_object",
                "fields": {"lat": "float", "lon": "float"},
                "constraints": ["lat is between -90 and 90", "lon is between -180 and 180"],
                "meaning": "geographic latitude/longitude coordinate",
            }
            value_objects["GridPoint"] = {
                "kind": "value_object",
                "fields": {"x": "number", "y": "number"},
                "constraints": ["city or simulation grid coordinate", "not a latitude/longitude pair"],
                "meaning": "project-local grid coordinate for facilities, tasks, maps, and simulations",
            }
            forbidden_equivalences.append(
                {
                    "left": "GridPoint",
                    "right": "GeoPoint",
                    "rule": "Do not construct GeoPoint directly from x/y or facility/task grid coordinates unless a kernel projection is declared.",
                    "diagnostic_code": "forbidden_value_object_equivalence",
                }
            )
            conversion_policies.append(
                {
                    "id": "grid_distance_without_projection",
                    "source": "GridPoint",
                    "target": "GridPoint",
                    "allowed_distance_models": ["grid_distance", "manhattan_distance"],
                    "rule": "When comparing facility/task grid coordinates, use a grid distance model, not geographic latitude/longitude validation.",
                }
            )
        if any(word in text for word in ["resource", "inventory", "capacity", "dispatch", "scenario"]):
            value_objects["StableIdentifier"] = {
                "kind": "value_object",
                "fields": {"id": "str"},
                "constraints": ["non-empty", "stable across serialization round trips"],
                "meaning": "public identifier used in fixtures, persistence, and reports",
            }
        canonical_type_owners = ContractCompiler._canonical_type_owners(value_objects, artifacts)
        return {
            "value_objects": value_objects,
            "canonical_type_owners": canonical_type_owners,
            "forbidden_equivalences": forbidden_equivalences,
            "conversion_policies": conversion_policies,
            "source": "deterministic_semantic_kernel_compiler",
        }

    @staticmethod
    def _canonical_type_owners(value_objects: Dict[str, Any], artifacts: List[str]) -> Dict[str, str]:
        if not value_objects:
            return {}
        packages = ContractCompiler._package_roots(artifacts)
        package = packages[0] if packages else ""
        preferred: Dict[str, str] = {}
        if package and f"{package}/domain/models.py" in artifacts:
            for name in value_objects:
                preferred[name] = f"{package}/domain/models.py"
        elif package and f"{package}/domain/invariants.py" in artifacts:
            for name in value_objects:
                preferred[name] = f"{package}/domain/invariants.py"
        else:
            for artifact in artifacts:
                if artifact.endswith(".py") and "/domain/" in artifact:
                    for name in value_objects:
                        preferred.setdefault(name, artifact)
        if package and f"{package}/domain/tasks.py" in artifacts:
            preferred.setdefault("TaskStatus", f"{package}/domain/tasks.py")
        if package and f"{package}/domain/fleet.py" in artifacts:
            preferred.setdefault("SpacecraftStatus", f"{package}/domain/fleet.py")
        return preferred

    @staticmethod
    def _public_behavior_flows(artifacts: List[str]) -> List[Dict[str, Any]]:
        packages = ContractCompiler._package_roots(artifacts)
        if not packages:
            return []
        package = packages[0]
        required = {
            f"{package}/domain/fleet.py",
            f"{package}/domain/tasks.py",
            f"{package}/core/scheduler.py",
            f"{package}/core/dispatch.py",
            f"{package}/core/simulation.py",
            f"{package}/io/storage.py",
        }
        if not required.issubset(set(artifacts)):
            return []
        code = f"""
from {package}.domain.fleet import FleetState, GridPoint, ResourceLedger, Spacecraft
from {package}.domain.tasks import Coordinate, MissionTask, TaskBatch, TaskPriority
from {package}.core.scheduler import create_schedule
from {package}.core.dispatch import dispatch_ready
from {package}.core.simulation import DispatchOrder, create_simulation
from {package}.io.storage import build_storage_bundle, deserialize_bundle, serialize_bundle

fleet = FleetState(spacecraft=(Spacecraft(identifier='ship-1', name='Aster', class_name='hauler', location=GridPoint(0, 0), resources=ResourceLedger(fuel=100, power=50, cargo_mass=0, crew=3)),))
tasks = TaskBatch(tasks=(MissionTask(identifier='task-1', name='Deliver relay', priority=TaskPriority.HIGH, location=Coordinate.from_grid(3, 4), duration_hours=2),))
schedule = create_schedule(fleet, tasks).to_dict()
assert schedule.get('assignments'), schedule
dispatch = dispatch_ready(fleet, tasks).to_dict()
assert dispatch.get('assignments'), dispatch
simulation = create_simulation(fleet, tasks)
simulation.queue_order(DispatchOrder('ship-1', 'task-1', GridPoint(3, 4)))
step = simulation.step(1)
assert step.state.tick >= 1, step.to_dict()
bundle = build_storage_bundle(fleet=fleet, tasks=tasks, simulation=step.state)
restored = deserialize_bundle(serialize_bundle(bundle, indent=None))
assert restored.fleet.tick == fleet.tick
assert restored.tasks.to_dict()['tasks'][0]['identifier']['id'] == 'task-1'
"""
        return [
            {
                "id": "mission_operations_public_flow",
                "kind": "python_behavior_probe",
                "description": "Construct a small mission fixture, schedule, dispatch, simulate one tick, and storage-roundtrip through public APIs.",
                "required_artifacts": sorted(required),
                "timeout": 30,
                "code": code.strip() + "\n",
            }
        ]

    @staticmethod
    def _formulas(goal: str, artifacts: List[str]) -> Dict[str, Any]:
        text = " ".join([goal, *artifacts]).lower()
        formulas: Dict[str, Any] = {
            "exact_numeric_assertion_policy": {
                "status": "guardrail",
                "rule": "Acceptance tests may assert exact numbers only when the value appears in a kernel fixture or is computed inside the test from public deterministic helpers.",
            }
        }
        for name in ("priority_score", "urgency_score", "route_score", "travel_time_minutes"):
            if name in text or name.replace("_", " ") in text:
                formulas[name] = {
                    "status": "unavailable_until_declared",
                    "rule": f"Do not hard-code exact {name} expectations unless a formula or fixture declares the value.",
                }
        return formulas

    @staticmethod
    def _public_api_policy(goal: str, artifacts: List[str]) -> Dict[str, Any]:
        package_roots = ContractCompiler._package_roots(artifacts)
        return {
            "package_roots": package_roots,
            "package_exports": "implementation_defined",
            "assertion_policy": "Acceptance tests may check importability and explicitly documented exports, but must not assert empty __all__ unless the user requested empty exports.",
            "version_policy": "Generated packages should expose a stable __version__ when practical.",
        }

    @staticmethod
    def _test_generation_policy() -> Dict[str, Any]:
        return {
            "mode": "kernel_derived",
            "semantic_sources": ["product_kernel.ontology", "product_kernel.formulas", "product_kernel.fixtures", "product_kernel.public_api_policy", "feature_slice.semantic_contract"],
            "forbidden": [
                "hard-coded numeric expectations without fixture/formula evidence",
                "package __all__ emptiness assertions without public_api_policy evidence",
                "GridPoint to GeoPoint equivalence without a declared projection",
                "new product rules invented by acceptance tests",
            ],
        }

    @staticmethod
    def _semantic_invariants(goal: str, artifacts: List[str], loc_budget: Dict[str, Any]) -> List[Dict[str, Any]]:
        text = " ".join([goal, *artifacts]).lower()
        invariants: List[Dict[str, Any]] = [
            {
                "id": "kernel_fixture_consistency",
                "kind": "fixture_consistency",
                "description": "Fixture values that describe the same entity must agree across tests, docs, and implementation examples.",
            },
            {
                "id": "semantic_ontology_consistency",
                "kind": "ontology",
                "description": "Value objects, coordinate systems, and public data meanings must follow Product Kernel ontology.",
            },
            {
                "id": "acceptance_has_kernel_source",
                "kind": "acceptance_source",
                "description": "Final acceptance assertions require a Product Kernel fixture, formula, ontology, or public API policy source.",
            },
            {
                "id": "public_behavior_examples",
                "kind": "behavior_examples",
                "description": "Public examples and tests must exercise declared package, CLI, persistence, and integration flows.",
            },
            {
                "id": "repair_exact_validation_required",
                "kind": "repair_validation",
                "description": "A final repair transaction is accepted only after exact locked validation passes in its isolated workspace.",
            },
        ]
        if loc_budget.get("enabled"):
            invariants.append(
                {
                    "id": "meaningful_scale_budget",
                    "kind": "loc_budget",
                    "min_total_loc": int(loc_budget.get("min_total_loc", 0) or 0),
                    "line_policy": loc_budget.get("line_policy", "non_empty_non_comment"),
                    "description": "The generated project must meet the explicit meaningful non-empty LOC budget without filler or dead code.",
                }
            )
        if any(word in text for word in ["route", "routing", "navigation", "distance", "coordinate", "pathfinding", "路径", "导航", "距离"]):
            invariants.append(
                {
                    "id": "route_fixture_distance_consistency",
                    "kind": "fixture_consistency",
                    "domain_terms": ["route", "distance", "coordinate", "leg"],
                    "description": "Route, coordinate, and distance fixtures must be internally consistent; do not derive expected totals from contradictory literals.",
                }
            )
        if any(word in text for word in ["save", "load", "persist", "storage", "scenario", "fixture", "io/", "persistence"]):
            invariants.append(
                {
                    "id": "persistence_round_trip_consistency",
                    "kind": "round_trip",
                    "description": "Persistence and scenario fixtures round-trip without losing identifiers, totals, or public fields.",
                }
            )
        return invariants

    def _slice_loc_budgets(
        self,
        kernel: ProductKernel,
        grouped: Dict[str, List[str]],
        test_artifacts: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        budget = self._kernel_loc_budget(kernel)
        if not budget.get("enabled"):
            return {}
        min_total = int(budget.get("min_total_loc", 0) or 0)
        if min_total <= 0:
            return {}
        keyed: Dict[str, List[str]] = {key: list(values) for key, values in grouped.items()}
        if test_artifacts:
            keyed["kernel_acceptance"] = list(test_artifacts)
        weighted = {
            key: max(1, sum(3 if path.endswith(".py") and not path.endswith("__init__.py") else 1 for path in paths))
            for key, paths in keyed.items()
        }
        total_weight = max(1, sum(weighted.values()))
        budgets: Dict[str, Dict[str, Any]] = {}
        assigned = 0
        ordered = list(weighted)
        for index, key in enumerate(ordered):
            if index == len(ordered) - 1:
                minimum = max(0, min_total - assigned)
            else:
                minimum = int(round(min_total * (weighted[key] / total_weight)))
                assigned += minimum
            budgets[key] = {
                "enabled": True,
                "min_total_loc": minimum,
                "line_policy": budget.get("line_policy", "non_empty_non_comment"),
                "no_padding": True,
                "hard_gate": False,
                "policy": "quality_signal",
            }
        return budgets

    @staticmethod
    def _kernel_loc_budget(kernel: ProductKernel) -> Dict[str, Any]:
        for invariant in kernel.semantic_invariants:
            if invariant.get("kind") == "loc_budget":
                return {
                    "enabled": True,
                    "min_total_loc": int(invariant.get("min_total_loc", 0) or 0),
                    "line_policy": invariant.get("line_policy", "non_empty_non_comment"),
                    "no_padding": True,
                    "hard_gate": False,
                    "policy": "quality_signal",
                }
        return {"enabled": False}

    @staticmethod
    def _uses_granular_slices(implementation_artifacts: List[str]) -> bool:
        return len(implementation_artifacts) >= 12

    def _slice_order(self, grouped: Dict[str, List[str]], granular: bool) -> List[str]:
        if not granular:
            return [
                "package_surface",
                "domain_foundation",
                "behavior_engine",
                "planning_intelligence",
                "persistence_flow",
                "public_interface",
                "app_integration",
            ]
        tier = {
            "package_surface": 0,
            "domain_": 1,
            "utility_": 1,
            "core_": 2,
            "planning_": 3,
            "io_": 3,
            "interface_": 4,
            "app_": 4,
        }

        def rank(key: str) -> tuple[int, str]:
            if key == "package_surface":
                return (0, key)
            for prefix, value in tier.items():
                if key.startswith(prefix):
                    return (value, key)
            return (5, key)

        return sorted(grouped, key=rank)

    @staticmethod
    def _granular_slice_key(artifact: str) -> str:
        parts = [part for part in artifact.replace("\\", "/").split("/") if part]
        name = parts[-1] if parts else ""
        stem = os.path.splitext(name)[0]
        if name == "__init__.py":
            return "package_surface"
        lowered = [part.lower() for part in parts]
        safe_stem = ContractCompiler._safe_slice_part(stem)
        if "domain" in lowered:
            return f"domain_{safe_stem}"
        if "core" in lowered:
            return f"core_{safe_stem}"
        if "planning" in lowered or "planner" in lowered or "ai" in lowered:
            return f"planning_{safe_stem}"
        if any(part in {"io", "storage", "persistence", "scenarios", "maps"} for part in lowered):
            return f"io_{safe_stem}"
        if any(part in {"interface", "cli", "api", "repl"} for part in lowered) or stem in {"cli", "main", "__main__"}:
            return f"interface_{safe_stem}"
        if "utils" in lowered or "util" in lowered:
            return f"utility_{safe_stem}"
        return f"app_{safe_stem}"

    def _dependencies_for_key(self, key: str, grouped: Dict[str, List[str]], granular: bool) -> List[str]:
        if not granular:
            return [dep for dep in self._dependencies_for(key) if dep in grouped]
        package = ["package_surface"] if "package_surface" in grouped and key != "package_surface" else []
        domain = sorted(dep for dep in grouped if dep.startswith("domain_"))
        utility = sorted(dep for dep in grouped if dep.startswith("utility_"))
        core = sorted(dep for dep in grouped if dep.startswith("core_"))
        planning = sorted(dep for dep in grouped if dep.startswith("planning_"))
        io = sorted(dep for dep in grouped if dep.startswith("io_"))
        if key == "package_surface":
            return []
        if key.startswith("domain_") or key.startswith("utility_"):
            return package
        if key.startswith("core_"):
            return _dedupe([*package, *domain, *utility])
        if key.startswith("planning_"):
            return _dedupe([*package, *domain, *utility, *core])
        if key.startswith("io_"):
            return _dedupe([*package, *domain, *utility, *core])
        if key.startswith("interface_"):
            return _dedupe([*package, *domain, *utility, *core, *planning, *io])
        if key.startswith("app_"):
            return _dedupe([*package, *domain, *utility, *core])
        return package

    def _interface_contract_for(
        self,
        key: str,
        owners: List[str],
        consumers: List[str],
        dependencies: List[str],
        size_budget: Dict[str, Any],
    ) -> Dict[str, Any]:
        public_modules = self._python_modules(owners, include_tests=False)
        return {
            "id": f"slice_contract:{key}",
            "producer_slice": key,
            "owner_artifacts": list(owners),
            "consumer_artifacts": list(consumers),
            "dependency_slices": list(dependencies),
            "public_modules": public_modules,
            "required_shapes": self._required_shapes_for(key, public_modules),
            "consumer_examples": [
                {
                    "consumer": artifact,
                    "uses": public_modules[:3],
                    "rule": "depend on public modules and stable values, not private implementation details",
                }
                for artifact in consumers[:8]
            ],
            "size_budget": dict(size_budget or {"enabled": False}),
            "ambiguity_policy": "If the kernel does not define a semantic rule, implement the smallest coherent public behavior and record evidence.",
        }

    @staticmethod
    def _semantic_contract_for(
        key: str,
        owners: List[str],
        consumers: List[str],
        dependencies: List[str],
        kernel: ProductKernel,
    ) -> Dict[str, Any]:
        ontology = dict(kernel.ontology or {})
        value_objects = dict(ontology.get("value_objects", {}) or {})
        forbidden = list(ontology.get("forbidden_equivalences", []) or [])
        conversion = list(ontology.get("conversion_policies", []) or [])
        owner_text = " ".join([key, *owners, *consumers]).lower()
        relevant_objects: Dict[str, Any] = {}
        for name, record in value_objects.items():
            lowered = name.lower()
            if (
                lowered in owner_text
                or (name == "GeoPoint" and any(term in owner_text for term in ["geo", "route", "routing", "dispatch", "incident"]))
                or (name == "GridPoint" and any(term in owner_text for term in ["grid", "facility", "task", "route", "routing", "dispatch", "scenario", "map"]))
                or (name == "StableIdentifier" and any(term in owner_text for term in ["resource", "scenario", "io", "save", "load", "dispatch"]))
            ):
                relevant_objects[name] = record
        if key == "kernel_acceptance":
            relevant_objects = value_objects
        formulas = {
            name: record
            for name, record in (kernel.formulas or {}).items()
            if name == "exact_numeric_assertion_policy" or name.lower() in owner_text or key == "kernel_acceptance"
        }
        return {
            "id": f"semantic_contract:{key}",
            "source": "product_kernel",
            "owner_artifacts": list(owners),
            "consumer_artifacts": list(consumers[:12]),
            "dependency_slices": list(dependencies),
            "value_objects": relevant_objects,
            "forbidden_equivalences": forbidden if relevant_objects else [],
            "conversion_policies": conversion if relevant_objects else [],
            "formulas": formulas,
            "public_api_policy": dict(kernel.public_api_policy or {}),
            "acceptance_policy": dict(kernel.test_generation_policy or {}),
            "worker_rules": [
                "Before using upstream fields, name their Product Kernel value object meaning.",
                "Do not equate value objects listed in forbidden_equivalences.",
                "Exact expected values must be generated from formulas or fixtures, never guessed.",
            ],
        }

    @staticmethod
    def _slice_smoke_for(key: str, owners: List[str]) -> List[Dict[str, Any]]:
        modules = ContractCompiler._python_modules(owners, include_tests=False)
        if not modules:
            return []
        return [{"id": f"{key}:import_public_modules", "kind": "python_import", "modules": modules, "timeout": 30}]

    @staticmethod
    def _python_modules(paths: Iterable[str], include_tests: bool = False) -> List[str]:
        modules: List[str] = []
        for path in paths:
            normalized = path.replace("\\", "/")
            if not normalized.endswith(".py"):
                continue
            if not include_tests and (normalized.startswith("tests/") or "/tests/" in f"/{normalized}"):
                continue
            stem = normalized[:-3]
            if stem.endswith("/__init__"):
                stem = stem[: -len("/__init__")]
            parts = [part for part in stem.split("/") if part]
            if all(part.isidentifier() for part in parts):
                module = ".".join(parts)
                if module and module not in modules:
                    modules.append(module)
        return modules

    @staticmethod
    def _required_shapes_for(key: str, public_modules: List[str]) -> List[Dict[str, Any]]:
        if not public_modules:
            return []
        if key == "package_surface":
            return [{"module": module, "shape": "importable_package_surface"} for module in public_modules]
        if key == "public_interface":
            return [{"module": module, "shape": "callable_main_or_parser_when_applicable"} for module in public_modules]
        if key == "persistence_flow":
            return [{"module": module, "shape": "round_trip_functions_or_repository_class"} for module in public_modules]
        if key == "planning_intelligence":
            return [{"module": module, "shape": "deterministic_plan_or_policy_api"} for module in public_modules]
        if key == "behavior_engine":
            return [{"module": module, "shape": "deterministic_step_or_run_api"} for module in public_modules]
        if key.startswith("domain_"):
            return [{"module": module, "shape": "canonical_domain_types_and_serialization"} for module in public_modules]
        if key.startswith("core_"):
            return [{"module": module, "shape": "deterministic_core_capability_api"} for module in public_modules]
        if key.startswith("planning_"):
            return [{"module": module, "shape": "deterministic_planning_capability_api"} for module in public_modules]
        if key.startswith("io_"):
            return [{"module": module, "shape": "fixture_or_persistence_capability_api"} for module in public_modules]
        if key.startswith("interface_"):
            return [{"module": module, "shape": "public_command_or_repl_api"} for module in public_modules]
        return [{"module": module, "shape": "importable_public_api"} for module in public_modules]

    @staticmethod
    def _interface_capabilities(team_id: str, required_shapes: List[Dict[str, Any]], public_modules: List[str]) -> List[str]:
        capabilities = [str(shape.get("shape", "")) for shape in required_shapes if shape.get("shape")]
        if not capabilities and public_modules:
            capabilities.append("importable_public_api")
        if team_id == "foundation":
            capabilities.append("package_surface")
        elif team_id == "domain_kernel":
            capabilities.append("canonical_domain_types")
        elif team_id == "core_engine":
            capabilities.append("deterministic_core_behavior")
        elif team_id == "planning_intelligence":
            capabilities.append("deterministic_planning_policy")
        elif team_id == "scenario_persistence":
            capabilities.append("scenario_and_persistence_round_trip")
        elif team_id == "public_interface":
            capabilities.append("public_cli_or_api_entrypoint")
        return _dedupe(capabilities)

    @staticmethod
    def _safe_slice_part(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value or "slice").strip("_").lower()
        return cleaned or "slice"

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        normalized = path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (name.startswith("test_") or "/tests/" in f"/{normalized}")

    @staticmethod
    def _package_roots(artifacts: Iterable[str]) -> List[str]:
        roots: List[str] = []
        for artifact in artifacts:
            parts = artifact.split("/")
            if len(parts) > 1 and parts[0].isidentifier() and parts[0] != "tests" and parts[0] not in roots:
                roots.append(parts[0])
        return roots

    @staticmethod
    def _cli_modules(artifacts: Iterable[str]) -> List[str]:
        modules: List[str] = []
        for artifact in artifacts:
            if not artifact.endswith(".py"):
                continue
            stem = artifact[:-3]
            parts = stem.split("/")
            if parts[-1] == "__main__":
                parts = parts[:-1]
            if parts[-1] in {"cli", "main"} or "interface" in parts:
                if all(part.isidentifier() for part in parts) and ".".join(parts) not in modules:
                    modules.append(".".join(parts))
        return modules[:4]

    @staticmethod
    def _slice_key(artifact: str) -> str:
        parts = [part.lower() for part in artifact.replace("\\", "/").split("/") if part]
        name = parts[-1] if parts else ""
        stem = os.path.splitext(name)[0]
        if name == "__init__.py":
            return "package_surface"
        if any(part in {"domain", "models", "schemas", "entities"} for part in parts):
            return "domain_foundation"
        if any(part in {"core", "engine", "rules", "simulation", "systems"} for part in parts):
            return "behavior_engine"
        if any(part in {"planning", "planner", "policies", "heuristics"} for part in parts):
            return "planning_intelligence"
        if "ai" in parts:
            return "planning_intelligence"
        if any(part in {"io", "storage", "persistence", "scenarios", "maps"} for part in parts):
            return "persistence_flow"
        if any(part in {"interface", "cli", "api", "repl"} for part in parts) or stem in {"cli", "main", "__main__"}:
            return "public_interface"
        return "app_integration"

    @staticmethod
    def _dependencies_for(key: str) -> List[str]:
        return {
            "package_surface": [],
            "domain_foundation": ["package_surface"],
            "behavior_engine": ["package_surface", "domain_foundation"],
            "planning_intelligence": ["package_surface", "domain_foundation", "behavior_engine"],
            "persistence_flow": ["package_surface", "domain_foundation", "behavior_engine"],
            "public_interface": [
                "package_surface",
                "domain_foundation",
                "behavior_engine",
                "planning_intelligence",
                "persistence_flow",
            ],
            "kernel_acceptance": [
                "package_surface",
                "domain_foundation",
                "behavior_engine",
                "planning_intelligence",
                "persistence_flow",
                "public_interface",
                "app_integration",
            ],
            "app_integration": ["package_surface", "domain_foundation", "behavior_engine"],
        }.get(key, [])

    @staticmethod
    def _invariants_for(key: str) -> List[str]:
        base = ["artifact_ownership", "import_safe", "no_placeholder_behavior"]
        if key in {"behavior_engine", "planning_intelligence", "persistence_flow", "public_interface", "app_integration"}:
            base.append("producer_consumer_shape")
        return base

    @staticmethod
    def _slice_title(key: str) -> str:
        return {
            "package_surface": "package export surface",
            "domain_foundation": "canonical domain foundation",
            "behavior_engine": "behavior engine flow",
            "planning_intelligence": "planning intelligence flow",
            "persistence_flow": "persistence and scenario flow",
            "public_interface": "public interface flow",
            "kernel_acceptance": "kernel acceptance tests",
            "app_integration": "application integration flow",
        }.get(key, f"{key} slice")
