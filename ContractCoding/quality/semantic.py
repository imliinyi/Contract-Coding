"""Semantic kernel acceptance compiler and contract linter."""

from __future__ import annotations

import os
import re
import ast
from typing import Any, Dict, Iterable, List, Tuple

from ContractCoding.contract.spec import ContractSpec, WorkItem, _dedupe


def compile_kernel_acceptance(
    workspace_dir: str,
    contract: ContractSpec,
    item: WorkItem,
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """Materialize kernel-derived acceptance tests into the item workspace.

    This deliberately replaces free-form final test authoring. The generated
    tests assert only universal contract facts: artifacts exist, package roots
    import, and the kernel policy itself is present. Product-specific exact
    values belong in explicit kernel fixtures/formulas; absent those, tests do
    not invent them.
    """

    changed: List[str] = []
    diagnostics: List[Dict[str, Any]] = []
    package_roots = _package_roots(contract.required_artifacts)
    kernel_record = {
        "ontology": contract.product_kernel.ontology,
        "formulas": contract.product_kernel.formulas,
        "public_api_policy": contract.product_kernel.public_api_policy,
        "test_generation_policy": contract.product_kernel.test_generation_policy,
        "acceptance_matrix": contract.product_kernel.acceptance_matrix,
    }
    public_flows = [
        {
            "id": flow.get("id"),
            "kind": flow.get("kind"),
            "code": flow.get("code"),
        }
        for flow in contract.product_kernel.flows
        if flow.get("kind") == "python_behavior_probe"
    ]
    for artifact in item.allowed_artifacts:
        if not artifact.endswith(".py"):
            continue
        path = os.path.join(workspace_dir, artifact)
        os.makedirs(os.path.dirname(path) or workspace_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_acceptance_test_source(contract, artifact, package_roots, kernel_record, public_flows))
        changed.append(artifact)
    if not changed:
        diagnostics.append(
            {
                "code": "kernel_acceptance_no_python_tests",
                "artifact": ",".join(item.allowed_artifacts),
                "message": "kernel acceptance item owns no Python test artifacts",
                "kernel_invariant": "tests_compile_kernel_acceptance",
            }
        )
    evidence = [
        "kernel_derived_acceptance:compiled",
        f"kernel_derived_acceptance:tests:{len(changed)}",
        "acceptance_semantic_source:product_kernel",
    ]
    return changed, evidence, diagnostics


def lint_contract_semantics(workspace_dir: str, contract: ContractSpec) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Check implementation/tests against frozen Product Kernel semantics."""

    evidence: List[str] = []
    diagnostics: List[Dict[str, Any]] = []
    ontology = dict(contract.product_kernel.ontology or {})
    value_objects = dict(ontology.get("value_objects", {}) or {})
    if "GeoPoint" in value_objects and "GridPoint" in value_objects:
        diagnostics.extend(_lint_gridpoint_geopoint_equivalence(workspace_dir, contract))
        evidence.append("semantic_lint:GeoPoint/GridPoint")
    diagnostics.extend(_lint_canonical_type_ownership(workspace_dir, contract))
    if (ontology.get("canonical_type_owners") or {}):
        evidence.append("semantic_lint:canonical_type_ownership")
    diagnostics.extend(_lint_acceptance_sources(workspace_dir, contract))
    if not diagnostics:
        evidence.append("semantic_lint:pass")
    return evidence, diagnostics


def lint_canonical_type_ownership_for_artifacts(
    workspace_dir: str,
    contract: ContractSpec,
    artifacts: Iterable[str],
    slice_id: str = "",
) -> Tuple[List[str], List[Dict[str, Any]]]:
    owners = dict((contract.product_kernel.ontology or {}).get("canonical_type_owners", {}) or {})
    if not owners:
        return [], []
    selected = [artifact for artifact in artifacts if artifact.endswith(".py")]
    diagnostics: List[Dict[str, Any]] = []
    evidence = ["semantic_lint:slice_canonical_type_ownership"]
    for artifact in selected:
        text = _read_text(os.path.join(workspace_dir, artifact))
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        for type_name, owner in owners.items():
            if type_name in definitions and artifact != owner:
                diagnostics.append(
                    {
                        "code": "canonical_type_redefined",
                        "artifact": artifact,
                        "message": f"{type_name} is kernel-owned by {owner}; this slice must import it instead of redefining it.",
                        "kernel_invariant": "canonical_type_ownership",
                        "slice_id": slice_id,
                        "required_artifacts": [owner, artifact] if owner else [artifact],
                    }
                )
            if artifact == owner and type_name not in definitions:
                diagnostics.append(
                    {
                        "code": "canonical_type_missing_from_owner",
                        "artifact": artifact,
                        "message": f"{artifact} owns canonical type {type_name} but does not define it.",
                        "kernel_invariant": "canonical_type_ownership",
                        "slice_id": slice_id,
                        "required_artifacts": [artifact],
                    }
                )
    return evidence, diagnostics


def semantic_kernel_delta(contract: ContractSpec, diagnostics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce a minimal kernel delta for semantic replans."""

    text = " ".join(
        str(diag.get("code", ""))
        + " "
        + str(diag.get("message", ""))
        + " "
        + str(diag.get("artifact", ""))
        for diag in diagnostics
    ).lower()
    delta: Dict[str, Any] = {"kind": "semantic_replan", "diagnostic_codes": _dedupe(diag.get("code", "") for diag in diagnostics)}
    if "latitude out of range" in text or "forbidden_value_object_equivalence" in text:
        delta["ontology_patch"] = {
            "value_objects": ["GeoPoint", "GridPoint"],
            "rule": "Separate geographic coordinates from grid coordinates; route grid entities with grid distance or add an explicit projection.",
            "affected_semantic_contracts": [
                "domain_foundation",
                "behavior_engine",
                "planning_intelligence",
                "domain_geo",
                "domain_facilities",
                "domain_tasks",
                "core_routing",
                "core_dispatch",
                "planning_optimizer",
                "kernel_acceptance",
            ],
        }
    if "ungrounded_acceptance_assertion" in text or "__all__" in text:
        delta["acceptance_patch"] = {
            "rule": "Regenerate acceptance from kernel fixtures/formulas/API policy; remove assertions without a kernel source.",
            "affected_semantic_contracts": ["kernel_acceptance"],
        }
    return delta


def _acceptance_test_source(
    contract: ContractSpec,
    artifact: str,
    package_roots: List[str],
    kernel_record: Dict[str, Any],
    public_flows: List[Dict[str, Any]],
) -> str:
    required = list(contract.required_artifacts)
    matrix_ids = [str(row.get("id", "")) for row in contract.product_kernel.acceptance_matrix]
    return (
        '"""Kernel-derived acceptance tests.\n\n'
        "This file is generated from Product Kernel acceptance. It must not\n"
        "introduce product rules that are absent from ontology, fixtures,\n"
        "formulas, or public_api_policy.\n"
        '"""\n\n'
        "import importlib\n"
        "import pathlib\n"
        "import unittest\n\n"
        f"TEST_ARTIFACT = {artifact!r}\n"
        f"PACKAGE_ROOTS = {package_roots!r}\n"
        f"REQUIRED_ARTIFACTS = {required!r}\n"
        f"ACCEPTANCE_MATRIX_IDS = {matrix_ids!r}\n"
        f"PRODUCT_KERNEL = {repr(kernel_record)}\n\n\n"
        f"PUBLIC_BEHAVIOR_FLOWS = {repr(public_flows)}\n\n\n"
        "class KernelDerivedAcceptance(unittest.TestCase):\n"
        "    def test_required_artifacts_exist(self):\n"
        "        for artifact in REQUIRED_ARTIFACTS:\n"
        "            self.assertTrue(pathlib.Path(artifact).exists(), artifact)\n\n"
        "    def test_package_roots_import(self):\n"
        "        for package in PACKAGE_ROOTS:\n"
        "            module = importlib.import_module(package)\n"
        "            self.assertIsNotNone(module)\n\n"
        "    def test_acceptance_sources_are_kernel_declared(self):\n"
        "        self.assertIn('artifact_coverage', ACCEPTANCE_MATRIX_IDS)\n"
        "        self.assertIn('compile_import', ACCEPTANCE_MATRIX_IDS)\n"
        "        policy = PRODUCT_KERNEL.get('test_generation_policy', {})\n"
        "        self.assertEqual(policy.get('mode'), 'kernel_derived')\n\n"
        "    def test_public_api_policy_is_not_invented_by_tests(self):\n"
        "        policy = PRODUCT_KERNEL.get('public_api_policy', {})\n"
        "        self.assertIn(policy.get('package_exports'), {'implementation_defined', 'explicit', 'empty'})\n\n\n"
        "    def test_public_behavior_flows(self):\n"
        "        for flow in PUBLIC_BEHAVIOR_FLOWS:\n"
        "            if flow.get('kind') != 'python_behavior_probe':\n"
        "                continue\n"
        "            namespace = {'__name__': f\"kernel_flow_{flow.get('id', 'flow')}\"}\n"
        "            exec(flow.get('code') or '', namespace)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )


def _lint_gridpoint_geopoint_equivalence(workspace_dir: str, contract: ContractSpec) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    production_artifacts = [
        artifact
        for artifact in contract.required_artifacts
        if artifact.endswith(".py") and artifact not in contract.test_artifacts
    ]
    patterns = [
        re.compile(r"GeoPoint\s*\(\s*lat\s*=\s*float\s*\([^)]*\.(?:x|y)\)", re.MULTILINE),
        re.compile(r"GeoPoint\s*\(\s*lat\s*=\s*float\s*\([^)]*\[['\"](?:x|y)['\"]\]", re.MULTILINE),
        re.compile(r"return\s+cls\s*\(\s*lat\s*=\s*float\s*\([^)]*\[['\"](?:x|y)['\"]\]", re.MULTILINE),
        re.compile(r"coerce_geo_point\s*\([^)]*\.(?:x|y)", re.MULTILINE),
    ]
    for artifact in production_artifacts:
        text = _read_text(os.path.join(workspace_dir, artifact))
        if not text:
            continue
        for pattern in patterns:
            if not pattern.search(text):
                continue
            diagnostics.append(
                {
                    "code": "forbidden_value_object_equivalence",
                    "artifact": artifact,
                    "message": "Product Kernel defines GridPoint and GeoPoint as distinct; this artifact appears to construct/consume GeoPoint directly from x/y grid coordinates.",
                    "kernel_invariant": "semantic_ontology_consistency",
                    "required_artifacts": [artifact],
                }
            )
            break
    return diagnostics


def _lint_canonical_type_ownership(workspace_dir: str, contract: ContractSpec) -> List[Dict[str, Any]]:
    owners = dict((contract.product_kernel.ontology or {}).get("canonical_type_owners", {}) or {})
    if not owners:
        return []
    diagnostics: List[Dict[str, Any]] = []
    production_artifacts = [
        artifact
        for artifact in contract.required_artifacts
        if artifact.endswith(".py") and artifact not in contract.test_artifacts
    ]
    definitions: Dict[str, List[str]] = {name: [] for name in owners}
    for artifact in production_artifacts:
        text = _read_text(os.path.join(workspace_dir, artifact))
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in definitions:
                definitions[node.name].append(artifact)
    for name, artifacts in definitions.items():
        if not artifacts:
            continue
        owner = str(owners.get(name, ""))
        wrong = [artifact for artifact in artifacts if artifact != owner]
        if wrong:
            diagnostics.append(
                {
                    "code": "canonical_type_redefined",
                    "artifact": ",".join(wrong),
                    "message": f"{name} is kernel-owned by {owner}; consumers must import it instead of redefining it.",
                    "kernel_invariant": "canonical_type_ownership",
                    "required_artifacts": [owner, *wrong] if owner else wrong,
                }
            )
    return diagnostics


def _lint_acceptance_sources(workspace_dir: str, contract: ContractSpec) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    formulas = dict(contract.product_kernel.formulas or {})
    exports_policy = str((contract.product_kernel.public_api_policy or {}).get("package_exports", "implementation_defined"))
    exact_guard = formulas.get("exact_numeric_assertion_policy", {})
    for artifact in contract.test_artifacts:
        path = os.path.join(workspace_dir, artifact)
        text = _read_text(path)
        if not text:
            continue
        if exports_policy != "empty" and re.search(r"__all__\s*==\s*\[\s*\]", text):
            diagnostics.append(
                {
                    "code": "ungrounded_acceptance_assertion",
                    "artifact": artifact,
                    "message": "Acceptance asserts empty __all__ but Product Kernel public_api_policy does not require empty package exports.",
                    "kernel_invariant": "acceptance_has_kernel_source",
                }
            )
        if exact_guard and re.search(r"\b(?:priority_score|urgency_score|route_score|travel_time_minutes)\b\s*==\s*[-]?\d+", text):
            diagnostics.append(
                {
                    "code": "ungrounded_numeric_acceptance_assertion",
                    "artifact": artifact,
                    "message": "Acceptance hard-codes a score/time number; exact numeric expectations require a kernel fixture or formula source.",
                    "kernel_invariant": "acceptance_has_kernel_source",
                }
            )
    return diagnostics


def _package_roots(artifacts: Iterable[str]) -> List[str]:
    roots: List[str] = []
    for artifact in artifacts:
        parts = artifact.split("/")
        if len(parts) > 1 and parts[0].isidentifier() and parts[0] != "tests" and parts[0] not in roots:
            roots.append(parts[0])
    return roots


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""
