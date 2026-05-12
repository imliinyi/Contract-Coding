"""Contract-aware tools for bounded agents.

These tools expose the "brain/hands" split directly to workers: the runtime
owns the contract and gates, while the agent can ask precise questions about
its current slice, dependency APIs, canonical type ownership, and declared
public behavior probes.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from typing import Any, Callable, Dict, List

from ContractCoding.contract.spec import ContractSpec, FeatureSlice, WorkItem
from ContractCoding.execution.workspace import get_current_workspace
from ContractCoding.knowledge.prompting import dependency_interfaces_for
from ContractCoding.tools.file_tool import WorkspaceFS


def build_contract_tools(
    workspace_dir: str,
    contract: ContractSpec,
    item: WorkItem,
    feature_slice: FeatureSlice | None,
) -> List[Callable]:
    def get_workspace_path() -> str:
        return WorkspaceFS(get_current_workspace(workspace_dir)).resolve(".")

    def contract_snapshot(section: str = "current") -> str:
        section = (section or "current").strip().lower()
        payload: Dict[str, Any] = {
            "work_item": {
                "id": item.id,
                "kind": item.kind,
                "slice_id": item.slice_id,
                "allowed_artifacts": list(item.allowed_artifacts),
                "locked_artifacts": list(item.locked_artifacts),
                "dependencies": list(item.dependencies),
            },
            "canonical_types": dict((contract.product_kernel.ontology or {}).get("canonical_type_owners", {}) or {}),
            "canonical_substrate": contract.canonical_substrate.to_record(),
            "public_behavior_flows": [
                {
                    "id": flow.get("id"),
                    "kind": flow.get("kind"),
                    "description": flow.get("description"),
                    "required_artifacts": flow.get("required_artifacts", []),
                }
                for flow in contract.product_kernel.flows
                if flow.get("kind") == "python_behavior_probe"
            ],
        }
        if section in {"current", "slice", "all"} and feature_slice is not None:
            subcontract = contract.team_subcontract_by_team_id().get(feature_slice.feature_team_id)
            capsule = contract.interface_capsule_by_team_id().get(feature_slice.feature_team_id)
            payload["feature_slice"] = {
                "id": feature_slice.id,
                "feature_team_id": feature_slice.feature_team_id,
                "owner_artifacts": list(feature_slice.owner_artifacts),
                "dependencies": list(feature_slice.dependencies),
                "interface_contract": dict(feature_slice.interface_contract),
                "semantic_contract": dict(feature_slice.semantic_contract),
                "slice_smoke": list(feature_slice.slice_smoke),
            }
            payload["team_subcontract"] = subcontract.to_record() if subcontract is not None else {}
            payload["team_interface_capsule"] = capsule.to_record() if capsule is not None else {}
        if section in {"current", "dependencies", "all"}:
            payload["dependency_interface_capsules"] = dependency_interfaces_for(contract, feature_slice)
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    contract_snapshot.openai_schema = {
        "type": "function",
        "function": {
            "name": "contract_snapshot",
            "description": "Returns the current work item contract, team subcontract, interface capsule, canonical type ownership, declared public behavior flows, and dependency capsules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "current, slice, dependencies, or all.",
                        "default": "current",
                    }
                },
                "required": [],
            },
        },
    }

    def inspect_module_api(module_name: str) -> str:
        module_path = _module_to_path(get_workspace_path(), module_name)
        if not module_path:
            return json.dumps({"error": f"module {module_name} not found under workspace"}, sort_keys=True)
        try:
            with open(module_path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
        except Exception as exc:
            return json.dumps({"error": str(exc), "module": module_name}, sort_keys=True)
        payload = {
            "module": module_name,
            "path": os.path.relpath(module_path, get_workspace_path()).replace("\\", "/"),
            "imports": _imports(tree),
            "classes": _classes(tree),
            "functions": _functions(tree),
            "all_exports": _all_exports(tree),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    inspect_module_api.openai_schema = {
        "type": "function",
        "function": {
            "name": "inspect_module_api",
            "description": "Statically inspects a Python module under the workspace and returns public classes, methods, functions, imports, and __all__ without importing it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_name": {"type": "string", "description": "Dotted Python module name such as pkg.domain.models."}
                },
                "required": ["module_name"],
            },
        },
    }

    def run_public_flow(flow_id: str = "all") -> str:
        selected = [
            flow
            for flow in contract.product_kernel.flows
            if flow.get("kind") == "python_behavior_probe" and (flow_id in {"", "all"} or flow.get("id") == flow_id)
        ]
        if not selected:
            return json.dumps({"message": f"no public behavior flow matched {flow_id!r}"}, sort_keys=True)
        records: List[Dict[str, Any]] = []
        for flow in selected:
            code = str(flow.get("code", "") or "")
            result = _run_python(code, get_workspace_path(), timeout=int(flow.get("timeout", 30) or 30))
            records.append(
                {
                    "id": flow.get("id"),
                    "returncode": result.returncode,
                    "ok": result.returncode == 0,
                    "tail": (result.stdout + result.stderr)[-2000:],
                }
            )
        return json.dumps({"flows": records, "ok": all(record["ok"] for record in records)}, ensure_ascii=False, indent=2)

    run_public_flow.openai_schema = {
        "type": "function",
        "function": {
            "name": "run_public_flow",
            "description": "Runs declared Product Kernel public behavior probes in the current workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "Specific flow id or all.", "default": "all"}
                },
                "required": [],
            },
        },
    }

    return [contract_snapshot, inspect_module_api, run_public_flow]


def _module_to_path(workspace_dir: str, module_name: str) -> str:
    rel = module_name.replace(".", "/")
    candidates = [
        os.path.join(workspace_dir, rel + ".py"),
        os.path.join(workspace_dir, rel, "__init__.py"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""


def _imports(tree: ast.AST) -> List[str]:
    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * int(node.level or 0) + (node.module or "")
            imports.extend(f"{module}.{alias.name}".strip(".") for alias in node.names)
    return sorted(set(imports))


def _classes(tree: ast.AST) -> List[Dict[str, Any]]:
    classes: List[Dict[str, Any]] = []
    body = tree.body if isinstance(tree, ast.Module) else []
    for node in body:
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        classes.append(
            {
                "name": node.name,
                "bases": [_name(base) for base in node.bases],
                "methods": [
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_")
                ],
                "fields": [
                    target.id
                    for child in node.body
                    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
                    for target in [child.target]
                ],
                "line": node.lineno,
            }
        )
    return classes


def _functions(tree: ast.AST) -> List[Dict[str, Any]]:
    functions: List[Dict[str, Any]] = []
    body = tree.body if isinstance(tree, ast.Module) else []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            functions.append({"name": node.name, "args": [arg.arg for arg in node.args.args], "line": node.lineno})
    return functions


def _all_exports(tree: ast.AST) -> List[str]:
    body = tree.body if isinstance(tree, ast.Module) else []
    for node in body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
    return []


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return ""


def _run_python(code: str, workspace_dir: str, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=workspace_dir,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess([sys.executable, "-c", code], 124, stdout=exc.stdout or "", stderr=(exc.stderr or "timeout"))
