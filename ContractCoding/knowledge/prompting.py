"""Prompt-engineering overlays and bounded worker context packets."""

from __future__ import annotations

from typing import Any, Dict, List

from ContractCoding.contract.spec import ContractSpec, FeatureSlice, WorkItem, _dedupe


CORE_SYSTEM_PROMPT = (
    "You are a bounded agent inside ContractCoding Runtime V5. "
    "The runtime only orchestrates durable flow, workspace isolation, promotion, final gates, and repair fallback. "
    "You own local design quality, dependency-interface inspection, implementation choices, validation, and evidence. "
    "The Product Kernel is frozen; use it as the source of product semantics. "
    "Your feature team works from a local subcontract and publishes/consumes compact interface capsules. "
    "Your slice is accepted by executable contract evidence, not by narrative completion."
)


KIND_OVERLAYS = {
    "worker": (
        "Before editing, call contract_snapshot and inspect dependency/canonical modules listed in dependency_interface_capsules. "
        "Satisfy the team_subcontract, team_interface_capsule, feature_slice.interface_contract, and feature_slice.slice_smoke. "
        "Implement only allowed_artifacts, "
        "and finish by calling submit_result."
    ),
    "capsule": (
        "Lock the producer team's interface capsule before implementation. "
        "Do not invent full internal APIs; expose capabilities, public modules, examples, and shape policy needed for async consumers. "
        "Submit only capsule evidence."
    ),
    "acceptance": (
        "Author tests as executable projections of the frozen kernel and dependency interfaces. "
        "Do not invent product semantics and do not weaken implementation requirements. "
        "Exact expected numbers, strings, and rankings must come from kernel fixtures, inspected dependency interfaces, "
        "or values computed in the test from public deterministic helpers."
    ),
    "repair": (
        "Handle exactly one repair transaction. Cluster diagnostics, patch the smallest legal production surface, "
        "leave locked tests unchanged, run the transaction validation_commands when possible, "
        "and submit concrete changed_files plus validation evidence."
    ),
}


def kind_for_item(item: WorkItem) -> str:
    if item.kind in {"capsule", "interface"}:
        return "capsule"
    if item.kind == "repair":
        return "repair"
    if item.kind == "acceptance":
        return "acceptance"
    return "worker"


def system_prompt_for(item: WorkItem) -> str:
    kind = kind_for_item(item)
    return " ".join(
        [
            CORE_SYSTEM_PROMPT,
            KIND_OVERLAYS.get(kind, KIND_OVERLAYS["worker"]),
            "Use tools for all file edits. Long outputs belong in files or concise evidence summaries.",
        ]
    )


def bounded_worker_packet(
    contract: ContractSpec,
    item: WorkItem,
    feature_slice: FeatureSlice | None,
    skills: List[Dict[str, Any]] | None = None,
    max_chars: int = 14000,
) -> Dict[str, Any]:
    """Build the strict agent packet for one work item.

    The agent gets the current slice contract, direct dependency interfaces,
    compact team context, and skill summaries. It does not receive the full
    contract graph, every sibling slice body, or full skill checklists.
    """

    team_id = item.feature_team_id or (feature_slice.feature_team_id if feature_slice else "")
    packet: Dict[str, Any] = {
        "goal": _excerpt(contract.goal, 1200),
        "context_policy": {
            "mode": "bounded_feature_slice_packet",
            "max_chars": max_chars,
            "omitted": [
                "full feature slice graph",
                "full team graph",
                "full skill checklists",
                "unrelated promotion history",
            ],
            "instruction": "Ask through tools only for files you need; do not infer hidden global context.",
        },
        "product_kernel": _kernel_for_slice(contract, feature_slice),
        "workflow_contract": workflow_contract_for(item),
        "team_subcontract": _compact_team_subcontract(team_subcontract_record_for(contract, item, feature_slice)),
        "team_slice_contracts": team_slice_contracts_for(contract, team_id, current_slice_id=item.slice_id),
        "team_interface_capsule": _compact_interface_capsule(interface_capsule_for_team(contract, team_id)),
        "feature_slice": _compact_feature_slice(feature_slice, current=True)
        if feature_slice
        else {"id": item.slice_id, "owner_artifacts": list(item.allowed_artifacts)},
        "work_item": _compact_work_item(item),
        "repair_transaction": _compact_repair_transaction(
            next(
                (transaction.to_record() for transaction in contract.repair_transactions if transaction.id == item.repair_transaction_id),
                {},
            )
        ),
        "locked_artifacts": list(item.locked_artifacts),
        "dependency_interface_capsules": dependency_interfaces_for(contract, feature_slice),
        "downstream_consumers": consumer_interfaces_for(contract, feature_slice),
        "required_preflight": required_preflight_for(contract, item, feature_slice),
        "progressive_skills": _compact_skills(skills or []),
    }
    return _fit_packet(packet, max_chars=max_chars)


def workflow_contract_for(item: WorkItem) -> Dict[str, Any]:
    kind = kind_for_item(item)
    common = [
        "Call contract_snapshot before coding and inspect dependency/canonical modules before using their shapes.",
        "Edit only allowed_artifacts; locked_artifacts are read-only.",
        "Pass slice smoke and interface-contract checks before expecting promotion.",
        "Keep modules import-safe and dependency-free unless the user explicitly allowed dependencies.",
        "Submit exact changed_files, validation evidence, residual risks, and blocked validation if any.",
    ]
    if kind == "acceptance":
        common.insert(0, "Compile tests from Product Kernel acceptance and declared slice contracts only.")
    if kind == "capsule":
        common.insert(0, "Lock the smallest producer capsule that lets other teams work asynchronously.")
        common.append("Do not specify private implementation details or whole class internals unless the public contract requires them.")
    if kind == "repair":
        common.insert(0, "Patch the failure fingerprint, not unrelated cleanup.")
        common.insert(1, "Treat repair validation commands and locked tests as exact acceptance; do not broaden the patch.")
        common.append("If no legal patch exists, call report_blocker with the reason.")
    return {
        "runtime_responsibility": [
            "schedule work",
            "isolate team workspace",
            "enforce artifact scope",
            "run deterministic gates",
            "promote verified owner artifacts",
            "open repair/replan fallback",
        ],
        "agent_responsibility": common,
        "agent_team_flow": [
            "team_lead reads the local team contract and dependency evidence",
            "slice_worker_pool edits the current allowed artifacts only",
            "interface_steward keeps exported producer contracts stable",
            "team_reviewer checks compile/import/smoke evidence before submit_result",
        ],
    }


def feature_team_record_for(contract: ContractSpec, item: WorkItem, feature_slice: FeatureSlice | None) -> Dict[str, Any]:
    team_id = item.feature_team_id or (feature_slice.feature_team_id if feature_slice else "")
    if not team_id:
        return {}
    team = contract.feature_team_by_id().get(team_id)
    return team.to_record() if team else {}


def team_subcontract_record_for(contract: ContractSpec, item: WorkItem, feature_slice: FeatureSlice | None) -> Dict[str, Any]:
    team_id = item.feature_team_id or (feature_slice.feature_team_id if feature_slice else "")
    if not team_id:
        return {}
    subcontract = contract.team_subcontract_by_team_id().get(team_id)
    return subcontract.to_record() if subcontract else {}


def interface_capsule_for_team(contract: ContractSpec, team_id: str) -> Dict[str, Any]:
    if not team_id:
        return {}
    capsule = contract.interface_capsule_by_team_id().get(team_id)
    if capsule is not None:
        return capsule.to_record()
    return {}


def team_slice_contracts_for(contract: ContractSpec, team_id: str, current_slice_id: str = "") -> List[Dict[str, Any]]:
    if not team_id:
        return []
    records: List[Dict[str, Any]] = []
    for feature_slice in contract.feature_slices:
        if feature_slice.feature_team_id != team_id:
            continue
        records.append(_compact_feature_slice(feature_slice, current=feature_slice.id == current_slice_id))
    return records


def dependency_interfaces_for(contract: ContractSpec, feature_slice: FeatureSlice | None) -> List[Dict[str, Any]]:
    if feature_slice is None:
        return []
    by_id = contract.slice_by_id()
    promotions = {promotion.slice_id: promotion for promotion in contract.promotions}
    interfaces: List[Dict[str, Any]] = []
    seen_team_capsules: set[str] = set()
    for dependency_id in feature_slice.dependencies:
        dependency = by_id.get(dependency_id)
        if dependency is None:
            continue
        promotion = promotions.get(dependency_id)
        dependency_team_id = dependency.feature_team_id
        interface_capsule: Dict[str, Any]
        if dependency_team_id in seen_team_capsules:
            interface_capsule = {"ref": f"capsule:{dependency_team_id}", "omitted": "already supplied for this dependency team"}
        else:
            interface_capsule = _compact_interface_capsule(interface_capsule_for_team(contract, dependency_team_id))
            seen_team_capsules.add(dependency_team_id)
        interfaces.append(
            {
                "slice_id": dependency.id,
                "feature_team_id": dependency_team_id,
                "title": dependency.title,
                "owner_artifacts": list(dependency.owner_artifacts[:8]),
                "owner_artifact_count": len(dependency.owner_artifacts),
                "interface_contract": _compact_interface_contract(dependency.interface_contract),
                "interface_capsule": interface_capsule,
                "slice_smoke": list(dependency.slice_smoke[:4]),
                "promotion_summary": promotion.summary if promotion else "",
                "producer_evidence": list((promotion.evidence if promotion else [])[:3]),
                "instruction": (
                    "Inspect these artifacts with read_file or inspect_symbol before relying on constructors, "
                    "fields, attributes, or helper functions."
                ),
            }
        )
    return interfaces


def required_preflight_for(contract: ContractSpec, item: WorkItem, feature_slice: FeatureSlice | None) -> Dict[str, Any]:
    modules: List[str] = []
    if feature_slice is not None:
        for dependency in dependency_interfaces_for(contract, feature_slice):
            capsule = dependency.get("interface_capsule", {}) or {}
            modules.extend(str(module) for module in capsule.get("public_modules", []) or [])
    for artifact in contract.canonical_substrate.owner_artifacts:
        module = _artifact_to_module(artifact)
        if module:
            modules.append(module)
    flows = [
        str(flow.get("id"))
        for flow in contract.product_kernel.flows
        if flow.get("kind") == "python_behavior_probe"
        and any(artifact in item.allowed_artifacts for artifact in flow.get("required_artifacts", []) or [])
    ]
    return {
        "must_call": ["contract_snapshot"],
        "inspect_module_api": _dedupe(modules)[:12],
        "run_public_flow_when_relevant": _dedupe(flows),
        "submit_rule": "submit_result evidence must cite the snapshot and inspected dependency/canonical modules used.",
    }


def consumer_interfaces_for(contract: ContractSpec, feature_slice: FeatureSlice | None) -> List[Dict[str, Any]]:
    if feature_slice is None:
        return []
    consumers: List[Dict[str, Any]] = []
    for candidate in contract.feature_slices:
        if feature_slice.id in candidate.dependencies:
            consumers.append(
                {
                    "slice_id": candidate.id,
                    "title": candidate.title,
                    "consumer_artifacts": list(candidate.owner_artifacts[:6]),
                    "consumer_artifact_count": len(candidate.owner_artifacts),
                    "consumer_interface_contract": _compact_interface_contract(candidate.interface_contract),
                    "expected_from_current_slice": list(feature_slice.owner_artifacts[:6]),
                }
            )
    return consumers[:8]


def _kernel_for_slice(contract: ContractSpec, feature_slice: FeatureSlice | None) -> Dict[str, Any]:
    refs = set((feature_slice.invariant_refs if feature_slice else []) or [])
    acceptance_refs = set((feature_slice.acceptance_refs if feature_slice else []) or [])
    fixture_refs = set((feature_slice.fixture_refs if feature_slice else []) or [])
    return {
        "status": contract.product_kernel.status,
        "canonical_substrate": contract.canonical_substrate.to_record(),
        "ontology": _compact_ontology(contract.product_kernel.ontology),
        "formulas": dict(list((contract.product_kernel.formulas or {}).items())[:8]),
        "public_api_policy": dict(contract.product_kernel.public_api_policy or {}),
        "test_generation_policy": dict(contract.product_kernel.test_generation_policy or {}),
        "schemas": [
            {
                "id": schema.get("id"),
                "package_roots": schema.get("package_roots"),
                "artifact_count": len(schema.get("artifacts", []) or []) if isinstance(schema.get("artifacts"), list) else None,
                "description": schema.get("description"),
            }
            for schema in contract.product_kernel.schemas[:4]
        ],
        "fixtures": [
            fixture
            for fixture in contract.product_kernel.fixtures
            if not fixture_refs or fixture.get("id") in fixture_refs
        ][:4],
        "invariants": [
            invariant
            for invariant in contract.product_kernel.invariants
            if not refs or invariant.get("id") in refs
        ][:8],
        "semantic_invariants": list(contract.product_kernel.semantic_invariants[:8]),
        "acceptance_matrix": [
            row
            for row in contract.product_kernel.acceptance_matrix
            if not acceptance_refs or row.get("id") in acceptance_refs
        ][:8],
        "public_paths": list(contract.product_kernel.public_paths[:6]),
    }


def _compact_feature_team(record: Dict[str, Any]) -> Dict[str, Any]:
    if not record:
        return {}
    contract = dict(record.get("team_contract", {}) or {})
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "slice_ids": list((record.get("slice_ids", []) or [])[:16]),
        "slice_count": len(record.get("slice_ids", []) or []),
        "dependencies": list(record.get("dependencies", []) or []),
        "consumer_team_ids": list(record.get("consumer_team_ids", []) or []),
        "local_done_contract": list(record.get("local_done_contract", []) or []),
        "coordination_mode": record.get("coordination_mode"),
        "team_contract": {
            "id": contract.get("id"),
            "internal_parallel_candidates": list((contract.get("internal_parallel_candidates", []) or [])[:16]),
            "internal_serial_edges": list((contract.get("internal_serial_edges", []) or [])[:16]),
            "agent_team_flow": list(contract.get("agent_team_flow", []) or []),
            "mock_policy": dict(contract.get("mock_policy", {}) or {}),
        },
    }


def _compact_team_subcontract(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    if not record:
        return {}
    return {
        "id": record.get("id"),
        "team_id": record.get("team_id"),
        "purpose": record.get("purpose"),
        "slice_ids": list((record.get("slice_ids", []) or [])[:16]),
        "owner_artifacts": list((record.get("owner_artifacts", []) or [])[:16]),
        "owned_concepts": list(record.get("owned_concepts", []) or []),
        "dependency_team_ids": list(record.get("dependency_team_ids", []) or []),
        "dependency_capsule_refs": list(record.get("dependency_capsule_refs", []) or []),
        "interface_capsule_refs": list(record.get("interface_capsule_refs", []) or []),
        "local_done_contract": list(record.get("local_done_contract", []) or []),
        "local_quality_gates": list((record.get("local_quality_gates", []) or [])[:8]),
        "internal_parallel_groups": list((record.get("internal_parallel_groups", []) or [])[:8]),
        "internal_serial_edges": list((record.get("internal_serial_edges", []) or [])[:12]),
        "agent_roles": list(record.get("agent_roles", []) or []),
        "context_policy": dict(record.get("context_policy", {}) or {}),
        "escalation_policy": dict(record.get("escalation_policy", {}) or {}),
    }


def _compact_feature_slice(feature_slice: FeatureSlice | None, *, current: bool = False) -> Dict[str, Any]:
    if feature_slice is None:
        return {}
    record: Dict[str, Any] = {
        "id": feature_slice.id,
        "title": feature_slice.title,
        "feature_team_id": feature_slice.feature_team_id,
        "owner_artifacts": list(feature_slice.owner_artifacts if current else feature_slice.owner_artifacts[:6]),
        "owner_artifact_count": len(feature_slice.owner_artifacts),
        "dependencies": list(feature_slice.dependencies),
        "fixture_refs": list(feature_slice.fixture_refs),
        "invariant_refs": list(feature_slice.invariant_refs),
        "acceptance_refs": list(feature_slice.acceptance_refs),
        "interface_contract": _compact_interface_contract(feature_slice.interface_contract),
        "semantic_contract": _compact_semantic_contract(feature_slice.semantic_contract),
        "slice_smoke": list(feature_slice.slice_smoke[:6]),
        "phase": feature_slice.phase,
        "conflict_keys": list(feature_slice.conflict_keys if current else feature_slice.conflict_keys[:6]),
    }
    if current:
        record["done_contract"] = list(feature_slice.done_contract)
    return record


def _compact_interface_contract(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    return {
        "id": record.get("id"),
        "producer_slice": record.get("producer_slice"),
        "feature_team_id": record.get("feature_team_id"),
        "interface_capsule_ref": record.get("interface_capsule_ref"),
        "owner_artifacts": list((record.get("owner_artifacts", []) or [])[:8]),
        "consumer_artifacts": list((record.get("consumer_artifacts", []) or [])[:8]),
        "dependency_slices": list(record.get("dependency_slices", []) or []),
        "public_modules": list((record.get("public_modules", []) or [])[:12]),
        "required_shapes": list((record.get("required_shapes", []) or [])[:12]),
        "canonical_imports": dict(record.get("canonical_imports", {}) or {}),
        "canonical_exports": dict(record.get("canonical_exports", {}) or {}),
        "consumer_examples": list((record.get("consumer_examples", []) or [])[:4]),
        "size_budget": dict(record.get("size_budget", {}) or {}),
        "ambiguity_policy": record.get("ambiguity_policy"),
    }


def _compact_semantic_contract(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    value_objects = dict(record.get("value_objects", {}) or {})
    return {
        "id": record.get("id"),
        "source": record.get("source"),
        "value_objects": dict(list(value_objects.items())[:8]),
        "canonical_type_owners": dict(record.get("canonical_type_owners", {}) or {}),
        "owned_canonical_types": dict(record.get("owned_canonical_types", {}) or {}),
        "forbidden_equivalences": list((record.get("forbidden_equivalences", []) or [])[:6]),
        "conversion_policies": list((record.get("conversion_policies", []) or [])[:6]),
        "formulas": dict(list((record.get("formulas", {}) or {}).items())[:8]),
        "public_api_policy": dict(record.get("public_api_policy", {}) or {}),
        "acceptance_policy": dict(record.get("acceptance_policy", {}) or {}),
        "worker_rules": list((record.get("worker_rules", []) or [])[:6]),
    }


def _compact_ontology(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    return {
        "value_objects": dict(list((record.get("value_objects", {}) or {}).items())[:8]),
        "canonical_type_owners": dict(record.get("canonical_type_owners", {}) or {}),
        "canonical_substrate": dict(record.get("canonical_substrate", {}) or {}),
        "forbidden_equivalences": list((record.get("forbidden_equivalences", []) or [])[:6]),
        "conversion_policies": list((record.get("conversion_policies", []) or [])[:6]),
        "source": record.get("source"),
    }


def _compact_interface_capsule(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    if not record:
        return {}
    fixtures = []
    for fixture in record.get("fixtures", []) or []:
        fixtures.append(
            {
                "id": fixture.get("id"),
                "owner_artifact_count": len(fixture.get("owner_artifacts", []) or []),
                "required_shapes": list((fixture.get("required_shapes", []) or [])[:12]),
            }
        )
    return {
        "id": record.get("id"),
        "team_id": record.get("team_id"),
        "version": record.get("version"),
        "status": record.get("status"),
        "consumer_team_ids": list((record.get("consumer_team_ids", []) or [])[:12]),
        "producer_slice_ids": list((record.get("producer_slice_ids", []) or [])[:16]),
        "public_modules": list((record.get("public_modules", []) or [])[:16]),
        "canonical_imports": dict(record.get("canonical_imports", {}) or {}),
        "capabilities": list((record.get("capabilities", []) or [])[:12]),
        "key_signatures": list((record.get("key_signatures", []) or [])[:12]),
        "examples": list((record.get("examples", []) or [])[:3]),
        "fixtures": fixtures[:3],
        "smoke": list((record.get("smoke", []) or [])[:8]),
        "contract_tests": list((record.get("contract_tests", []) or [])[:8]),
        "compatibility": dict(record.get("compatibility", {}) or {}),
    }


def _compact_work_item(item: WorkItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "slice_id": item.slice_id,
        "title": item.title,
        "allowed_artifacts": list(item.allowed_artifacts),
        "dependencies": list(item.dependencies),
        "kind": item.kind,
        "phase": item.phase,
        "team_id": item.team_id,
        "feature_team_id": item.feature_team_id,
        "conflict_keys": list(item.conflict_keys),
        "locked_artifacts": list(item.locked_artifacts),
        "repair_transaction_id": item.repair_transaction_id,
        "attempts": item.attempts,
        "diagnostics": list(item.diagnostics[-3:]),
    }


def _compact_repair_transaction(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    if not record:
        return {}
    return {
        "id": record.get("id"),
        "failure_fingerprint": record.get("failure_fingerprint"),
        "root_invariant": record.get("root_invariant"),
        "allowed_artifacts": list(record.get("allowed_artifacts", []) or []),
        "locked_tests": list(record.get("locked_tests", []) or []),
        "patch_plan": list(record.get("patch_plan", []) or []),
        "expected_behavior_delta": record.get("expected_behavior_delta"),
        "validation_commands": list(record.get("validation_commands", []) or []),
        "last_validation": record.get("last_validation", {}),
        "status": record.get("status"),
        "attempts": record.get("attempts"),
        "no_progress_count": record.get("no_progress_count"),
    }


def _compact_skills(skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": skill.get("name"),
            "summary": skill.get("summary"),
            "must": list((skill.get("checklist", []) or [])[:3]),
            "avoid": list((skill.get("forbidden", []) or [])[:2]),
        }
        for skill in skills
    ]


def _fit_packet(packet: Dict[str, Any], *, max_chars: int) -> Dict[str, Any]:
    """Degrade optional context deterministically until it fits the budget."""

    import json

    def size() -> int:
        return len(json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    if size() <= max_chars:
        return packet
    packet["downstream_consumers"] = packet.get("downstream_consumers", [])[:3]
    if size() <= max_chars:
        return packet
    packet["team_slice_contracts"] = [
        row
        for row in packet.get("team_slice_contracts", [])
        if row.get("id") == packet.get("feature_slice", {}).get("id")
    ]
    if size() <= max_chars:
        return packet
    packet["dependency_interface_capsules"] = packet.get("dependency_interface_capsules", [])[:4]
    if size() <= max_chars:
        return packet
    packet["product_kernel"]["public_paths"] = packet.get("product_kernel", {}).get("public_paths", [])[:2]
    packet["product_kernel"]["semantic_invariants"] = packet.get("product_kernel", {}).get("semantic_invariants", [])[:3]
    if size() <= max_chars:
        return packet
    packet["context_policy"]["truncated"] = True
    packet["goal"] = _excerpt(str(packet.get("goal", "")), 500)
    if size() <= max_chars:
        return packet
    packet["dependency_interface_capsules"] = _minimal_dependency_capsules(packet.get("dependency_interface_capsules", []))
    packet["team_slice_contracts"] = _minimal_team_slice_contracts(packet.get("team_slice_contracts", []), packet.get("feature_slice", {}).get("id", ""))
    packet["team_interface_capsule"] = _minimal_interface_capsule(packet.get("team_interface_capsule", {}))
    packet["product_kernel"] = _minimal_kernel(packet.get("product_kernel", {}))
    if size() <= max_chars:
        return packet
    packet["downstream_consumers"] = []
    packet["team_subcontract"] = {
        "id": packet.get("team_subcontract", {}).get("id"),
        "team_id": packet.get("team_subcontract", {}).get("team_id"),
        "dependency_team_ids": packet.get("team_subcontract", {}).get("dependency_team_ids", []),
        "interface_capsule_refs": packet.get("team_subcontract", {}).get("interface_capsule_refs", []),
    }
    if size() <= max_chars:
        return packet
    packet["progressive_skills"] = [
        {
            "name": skill.get("name"),
            "summary": skill.get("summary"),
            "must": list((skill.get("must", []) or [])[:1]),
            "avoid": list((skill.get("avoid", []) or [])[:1]),
        }
        for skill in packet.get("progressive_skills", [])
    ]
    packet["dependency_interface_capsules"] = packet.get("dependency_interface_capsules", [])[:2]
    packet["team_slice_contracts"] = packet.get("team_slice_contracts", [])[:1]
    packet["feature_slice"] = _minimal_current_feature_slice(packet.get("feature_slice", {}))
    packet["context_policy"]["hard_truncated"] = True
    return packet


def _excerpt(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[truncated]"


def _artifact_to_module(artifact: str) -> str:
    normalized = str(artifact or "").replace("\\", "/")
    if not normalized.endswith(".py"):
        return ""
    stem = normalized[:-3]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    parts = [part for part in stem.split("/") if part]
    if parts and all(part.isidentifier() for part in parts):
        return ".".join(parts)
    return ""


def _minimal_dependency_capsules(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_teams: set[str] = set()
    for record in records[:12]:
        team_id = str(record.get("feature_team_id", ""))
        include_team_interface = team_id not in seen_teams
        seen_teams.add(team_id)
        out.append(
            {
                "slice_id": record.get("slice_id"),
                "feature_team_id": team_id,
                "owner_artifacts": list(record.get("owner_artifacts", [])[:4]),
                "public_modules": list(
                    ((record.get("interface_contract", {}) or {}).get("public_modules", []) or [])[:4]
                ),
                "required_shapes": list(
                    ((record.get("interface_contract", {}) or {}).get("required_shapes", []) or [])[:4]
                ),
                "interface_capsule": _minimal_interface_capsule(record.get("interface_capsule", {}))
                if include_team_interface
                else {"ref": f"capsule:{team_id}"},
                "instruction": record.get("instruction"),
            }
        )
    return out


def _minimal_team_slice_contracts(records: List[Dict[str, Any]], current_slice_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for record in records:
        if record.get("id") != current_slice_id:
            continue
        out.append(
            {
                "id": record.get("id"),
                "owner_artifacts": record.get("owner_artifacts", []),
                "dependencies": record.get("dependencies", []),
                "public_modules": ((record.get("interface_contract", {}) or {}).get("public_modules", []) or [])[:8],
                "required_shapes": ((record.get("interface_contract", {}) or {}).get("required_shapes", []) or [])[:8],
                "slice_smoke": record.get("slice_smoke", [])[:4],
            }
        )
    return out


def _minimal_current_feature_slice(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    interface_contract = dict(record.get("interface_contract", {}) or {})
    semantic_contract = dict(record.get("semantic_contract", {}) or {})
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "feature_team_id": record.get("feature_team_id"),
        "owner_artifacts": list(record.get("owner_artifacts", []) or []),
        "dependencies": list(record.get("dependencies", []) or []),
        "interface_contract": {
            "id": interface_contract.get("id"),
            "interface_capsule_ref": interface_contract.get("interface_capsule_ref"),
            "public_modules": list((interface_contract.get("public_modules", []) or [])[:8]),
            "required_shapes": list((interface_contract.get("required_shapes", []) or [])[:8]),
            "dependency_slices": list(interface_contract.get("dependency_slices", []) or []),
            "canonical_imports": dict(interface_contract.get("canonical_imports", {}) or {}),
        },
        "semantic_contract": {
            "id": semantic_contract.get("id"),
            "value_objects": dict(list((semantic_contract.get("value_objects", {}) or {}).items())[:4]),
            "forbidden_equivalences": list((semantic_contract.get("forbidden_equivalences", []) or [])[:3]),
            "conversion_policies": list((semantic_contract.get("conversion_policies", []) or [])[:3]),
            "formulas": dict(list((semantic_contract.get("formulas", {}) or {}).items())[:4]),
        },
        "slice_smoke": list((record.get("slice_smoke", []) or [])[:4]),
        "phase": record.get("phase"),
        "conflict_keys": list((record.get("conflict_keys", []) or [])[:8]),
        "done_contract": list((record.get("done_contract", []) or [])[:6]),
    }


def _minimal_interface_capsule(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    if not record:
        return {}
    if record.get("ref"):
        return {"ref": record.get("ref")}
    return {
        "id": record.get("id"),
        "team_id": record.get("team_id"),
        "version": record.get("version"),
        "status": record.get("status"),
        "public_modules": list((record.get("public_modules", []) or [])[:8]),
        "canonical_imports": dict(record.get("canonical_imports", {}) or {}),
        "capabilities": list((record.get("capabilities", []) or [])[:8]),
        "key_signatures": list((record.get("key_signatures", []) or [])[:6]),
        "examples": list((record.get("examples", []) or [])[:1]),
    }


def _minimal_kernel(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record or {})
    return {
        "status": record.get("status"),
        "ontology": _compact_ontology(record.get("ontology", {})),
        "formulas": dict(list((record.get("formulas", {}) or {}).items())[:4]),
        "public_api_policy": dict(record.get("public_api_policy", {}) or {}),
        "invariants": list((record.get("invariants", []) or [])[:4]),
        "acceptance_matrix": list((record.get("acceptance_matrix", []) or [])[:4]),
        "semantic_invariants": list((record.get("semantic_invariants", []) or [])[:2]),
    }
