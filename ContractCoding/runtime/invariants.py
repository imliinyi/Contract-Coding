"""Runtime-owned invariant checks.

These checks are deliberately outside verifier prompts. They turn "the agent
said it is done" into "the produced artifact satisfies basic system facts."
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
import os
import py_compile
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List

from ContractCoding.contract.work_item import WorkItem
from ContractCoding.runtime.test_discovery import TestCommandDiscoverer
from ContractCoding.tools.artifacts import ArtifactMetadataStore


@dataclass
class InvariantResult:
    errors: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class InvariantChecker:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.metadata_store = ArtifactMetadataStore(self.workspace_dir)

    def check_implementation(self, item: WorkItem, payload: Dict[str, Any] | None = None) -> InvariantResult:
        return self.check_self_check(item, payload)

    def check_self_check(self, item: WorkItem, payload: Dict[str, Any] | None = None) -> InvariantResult:
        """Run the implicit file/method/class-level check for one WorkItem."""

        payload = payload or {}
        result = InvariantResult()
        if self._is_integration_gate(item):
            return self._check_integration_gate(item)
        if self._is_scope_gate(item):
            return self._check_scope_gate(item)
        if item.kind not in {"coding", "research", "doc", "data", "ops", "eval"}:
            return result

        self._check_changed_file_scope(item, payload, result)
        for artifact in item.target_artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                result.errors.append(f"Target artifact missing for work item {item.id}: {artifact}")
                continue
            result.evidence.append(f"Target artifact exists: {artifact}.")

            version = self.metadata_store.get_version(path)
            if version is not None:
                result.evidence.append(f"Artifact metadata present for {artifact}: version {version}.")

            if item.kind == "coding" and artifact.endswith(".py"):
                self._check_python_artifact(artifact, path, result)
                placeholder_hits = self._placeholder_hits([artifact])
                if placeholder_hits:
                    result.errors.append(
                        f"Self-check failed: placeholder implementation remains in {artifact}."
                    )
            elif self._is_textual_artifact(artifact):
                placeholder_hits = self._placeholder_hits([artifact])
                if placeholder_hits:
                    result.errors.append(
                        f"Self-check failed: placeholder content remains in {artifact}."
                    )
                if item.kind == "research":
                    self._check_research_artifact(artifact, path, result)
                elif item.kind == "doc":
                    self._check_doc_artifact(artifact, path, result)
                elif item.kind == "data":
                    self._check_data_artifact(artifact, path, result)
                elif item.kind == "ops":
                    self._check_ops_artifact(artifact, path, result)
        return result

    def check_verification_preflight(self, item: WorkItem) -> InvariantResult:
        result = InvariantResult()
        if self._is_integration_gate(item):
            return self._check_integration_gate(item)
        if self._is_scope_gate(item):
            return self._check_scope_gate(item)
        if item.kind not in {"coding", "research", "doc", "data", "ops", "eval"}:
            return result
        for artifact in item.target_artifacts:
            if not os.path.exists(os.path.join(self.workspace_dir, artifact)):
                result.errors.append(f"Cannot verify missing target artifact for {item.id}: {artifact}")
        return result

    def _check_research_artifact(self, artifact: str, path: str, result: InvariantResult) -> None:
        content = self._read_text(path)
        if content is None:
            return
        if len(content.strip()) < 120:
            result.errors.append(f"Research artifact is too short to contain meaningful evidence: {artifact}.")
        if not any(marker in content.lower() for marker in ("source", "evidence", "claim", "citation", "reference", "uncertain")):
            result.evidence.append(
                f"Research artifact {artifact} exists, but source/claim markers are sparse; LLM verifier should inspect it."
            )
        else:
            result.evidence.append(f"Research evidence structure detected in {artifact}.")

    def _check_doc_artifact(self, artifact: str, path: str, result: InvariantResult) -> None:
        content = self._read_text(path)
        if content is None:
            return
        if len(content.strip()) < 80:
            result.errors.append(f"Document artifact is too short to satisfy a substantive writing task: {artifact}.")
        if "#" in content or "\n-" in content or "\n1." in content:
            result.evidence.append(f"Document structure markers detected in {artifact}.")

    def _check_data_artifact(self, artifact: str, path: str, result: InvariantResult) -> None:
        content = self._read_text(path)
        if content is None:
            return
        if len(content.strip()) < 80:
            result.errors.append(f"Data artifact is too short to record reproducible analysis: {artifact}.")
        if any(marker in content.lower() for marker in ("schema", "row", "count", "metric", "sanity", "summary")):
            result.evidence.append(f"Data-analysis sanity markers detected in {artifact}.")

    def _check_ops_artifact(self, artifact: str, path: str, result: InvariantResult) -> None:
        content = self._read_text(path)
        if content is None:
            return
        lower = content.lower()
        if not any(marker in lower for marker in ("dry-run", "dry run", "rollback", "approval", "risk")):
            result.errors.append(
                f"Ops artifact must include dry-run/rollback/approval/risk evidence before execution: {artifact}."
            )
        else:
            result.evidence.append(f"Ops safety markers detected in {artifact}.")

    def _check_python_artifact(self, artifact: str, path: str, result: InvariantResult) -> None:
        try:
            py_compile.compile(path, doraise=True)
            result.evidence.append(f"Python syntax validation passed for {artifact}.")
        except py_compile.PyCompileError as exc:
            result.errors.append(f"Python syntax validation failed for {artifact}: {exc.msg}")
            return

        if self._is_python_test_artifact(artifact):
            isolation_errors = self._test_isolation_violations(artifact)
            if isolation_errors:
                result.errors.extend(isolation_errors)
                return
            test_error, test_evidence = self._run_python_unittest(artifact)
            if test_error:
                result.errors.append(test_error)
            if test_evidence:
                result.evidence.append(test_evidence)
        else:
            import_error, import_evidence = self._run_python_import_check(artifact)
            if import_error:
                result.errors.append(import_error)
            if import_evidence:
                result.evidence.append(import_evidence)

    def _check_changed_file_scope(
        self,
        item: WorkItem,
        payload: Dict[str, Any],
        result: InvariantResult,
    ) -> None:
        changed_files = [
            self._normalize_path(path)
            for path in (payload.get("changed_files", []) or [])
            if str(path).strip()
        ]
        if not changed_files:
            return

        allowed = {self._normalize_path(path) for path in item.target_artifacts}
        for artifact in (payload.get("wave_allowed_artifacts", []) or []):
            if not str(artifact).strip():
                continue
            normalized = self._normalize_path(artifact)
            allowed.add(normalized)
            allowed.add(self._normalize_path(f".contractcoding/artifacts/{normalized}.json"))
        for artifact in item.target_artifacts:
            normalized = self._normalize_path(artifact)
            allowed.add(self._normalize_path(f".contractcoding/artifacts/{normalized}.json"))

        unexpected = [
            path
            for path in changed_files
            if path not in allowed and not self._is_runtime_side_effect(path)
        ]
        if unexpected:
            result.errors.append(
                f"Changed files outside work-item artifact scope for {item.id}: {', '.join(unexpected)}"
            )

    def _check_integration_gate(self, item: WorkItem) -> InvariantResult:
        result = InvariantResult()
        required = [self._normalize_path(path) for path in item.inputs.get("required_artifacts", [])]
        python_artifacts = [
            path
            for path in [self._normalize_path(path) for path in item.inputs.get("python_artifacts", [])]
            if path.endswith(".py")
        ]
        package_roots = [self._normalize_path(path) for path in item.inputs.get("package_roots", [])]
        requires_tests = bool(item.inputs.get("requires_tests", False))

        missing = [path for path in required if not os.path.exists(os.path.join(self.workspace_dir, path))]
        if missing:
            result.errors.append(f"Integration gate failed: target artifact(s) missing: {', '.join(missing)}")
        else:
            result.evidence.append(f"Integration gate found all {len(required)} target artifact(s).")

        unexpected = self._unexpected_source_files(required, item.inputs.get("allowed_extra_paths", []))
        if unexpected:
            result.errors.append(f"Integration gate failed: unexpected source write(s): {', '.join(unexpected[:20])}")

        placeholder_hits = self._placeholder_hits(required)
        if placeholder_hits:
            result.errors.append(
                f"Integration gate failed: placeholder implementation remains: {', '.join(placeholder_hits[:20])}"
            )

        loc_total = self._count_loc(required)
        result.evidence.append(f"LOC telemetry: {loc_total} non-empty line(s) across required artifacts.")

        compile_error, compile_evidence = self._run_package_compile(package_roots, python_artifacts)
        if compile_error:
            result.errors.append(compile_error)
        if compile_evidence:
            result.evidence.append(compile_evidence)

        import_errors, import_evidence = self._run_import_all(python_artifacts)
        result.errors.extend(import_errors)
        result.evidence.extend(import_evidence)

        contract_errors, contract_evidence = self._check_consumer_contracts(python_artifacts)
        result.errors.extend(contract_errors)
        result.evidence.extend(contract_evidence)

        test_error, test_evidence = self._run_unittest_discover_if_required(python_artifacts, requires_tests)
        if test_error:
            result.errors.append(test_error)
        if test_evidence:
            result.evidence.append(test_evidence)

        behavior_errors, behavior_evidence = self._check_product_behavior_contract(
            item.inputs.get("product_behavior", {}),
            required_artifacts=required,
            python_artifacts=python_artifacts,
        )
        result.errors.extend(behavior_errors)
        result.evidence.extend(behavior_evidence)

        self._write_integration_report(item, result, required, loc_total)
        return result

    def _check_scope_gate(self, item: WorkItem) -> InvariantResult:
        result = InvariantResult()
        scope_id = str(item.inputs.get("scope_id") or item.scope_id or item.id.replace("gate:", ""))
        scope_artifacts = [
            self._normalize_path(path)
            for path in item.inputs.get("scope_artifacts", [])
        ]
        test_artifacts = [
            self._normalize_path(path)
            for path in item.inputs.get("test_artifacts", [])
        ]
        interface_artifacts = [
            self._normalize_path(path)
            for path in item.inputs.get("interface_artifacts", [])
        ]
        python_artifacts = [
            self._normalize_path(path)
            for path in item.inputs.get("python_artifacts", [*scope_artifacts, *test_artifacts])
            if self._normalize_path(path).endswith(".py")
        ]
        deterministic_checks = {
            str(value).strip().lower()
            for value in item.inputs.get("deterministic_checks", [])
            if str(value).strip()
        } or {"artifact_coverage", "syntax_import", "functional_smoke", "placeholder_scan"}
        run_scope_tests = "scope_tests" in deterministic_checks or "scope_test" in deterministic_checks
        if not run_scope_tests:
            python_artifacts = [
                artifact
                for artifact in python_artifacts
                if artifact not in set(test_artifacts)
            ]
        required = self._dedupe(
            [
                *scope_artifacts,
                *(test_artifacts if run_scope_tests else []),
                *interface_artifacts,
            ]
        )
        requires_tests = bool(item.inputs.get("requires_tests", False))

        missing = [path for path in required if not os.path.exists(os.path.join(self.workspace_dir, path))]
        if missing:
            result.errors.append(f"Scope gate `{scope_id}` failed: required artifact(s) missing: {', '.join(missing)}")
        else:
            result.evidence.append(f"Scope gate `{scope_id}` found all {len(required)} required artifact(s).")

        if "placeholder_scan" in deterministic_checks:
            placeholder_hits = self._placeholder_hits([*scope_artifacts, *(test_artifacts if run_scope_tests else [])])
            if placeholder_hits:
                result.errors.append(
                    f"Scope gate `{scope_id}` failed: placeholder implementation remains: {', '.join(placeholder_hits[:20])}"
                )

        if "skip_interface_conformance" not in deterministic_checks:
            interface_errors, interface_evidence = self._check_interface_conformance(
                item.inputs.get("interface_specs", [])
            )
            result.errors.extend(f"Scope gate `{scope_id}` failed: {error}" for error in interface_errors)
            result.evidence.extend(interface_evidence)

        if "syntax_import" in deterministic_checks or "compile_import" in deterministic_checks:
            compile_error, compile_evidence = self._run_package_compile([], python_artifacts)
            if compile_error:
                result.errors.append(f"Scope gate `{scope_id}` failed: {compile_error}")
            if compile_evidence:
                result.evidence.append(compile_evidence)

            import_errors, import_evidence = self._run_import_all(python_artifacts)
            result.errors.extend(f"Scope gate `{scope_id}` failed: {error}" for error in import_errors)
            result.evidence.extend(import_evidence)

        existing_tests = [path for path in test_artifacts if os.path.exists(os.path.join(self.workspace_dir, path))]
        if run_scope_tests:
            if requires_tests and not existing_tests:
                result.errors.append(f"Scope gate `{scope_id}` failed: required scope tests are missing.")
            for test_artifact in existing_tests:
                test_error, test_evidence = self._run_python_unittest(test_artifact)
                if test_error:
                    result.errors.append(f"Scope gate `{scope_id}` failed: {test_error}")
                if test_evidence:
                    result.evidence.append(test_evidence)
        elif test_artifacts:
            result.evidence.append(
                f"Scope gate `{scope_id}` used smoke depth; {len(test_artifacts)} scope test artifact(s) deferred to hardening/final."
            )

        if "functional_smoke" in deterministic_checks:
            contract_errors, contract_evidence = self._check_consumer_contracts([*scope_artifacts, *interface_artifacts])
            result.errors.extend(f"Scope gate `{scope_id}` failed: {error}" for error in contract_errors)
            result.evidence.extend(contract_evidence)
            if not contract_errors:
                result.evidence.append(
                    f"Scope gate `{scope_id}` functional smoke passed via artifact/syntax/import/consumer-contract checks."
                )

        self._write_scope_report(item, result, scope_id, required)
        return result

    def _check_interface_conformance(self, interface_specs: Iterable[Dict[str, Any]]) -> tuple[List[str], List[str]]:
        errors: List[str] = []
        evidence: List[str] = []
        for spec in interface_specs or []:
            if not isinstance(spec, dict):
                continue
            artifact = self._normalize_path(str(spec.get("artifact", "")))
            if not artifact:
                continue
            parsed_by_artifact: Dict[str, tuple[set[str], set[str], Dict[str, ast.ClassDef]]] = {}
            spec_errors_before = len(errors)

            def parsed_interface_artifact(target_artifact: str):
                target_artifact = self._normalize_path(str(target_artifact or artifact))
                if target_artifact in parsed_by_artifact:
                    return parsed_by_artifact[target_artifact]
                path = os.path.join(self.workspace_dir, target_artifact)
                if not os.path.exists(path):
                    errors.append(f"Interface {spec.get('id', 'unknown')} artifact is missing: {target_artifact}")
                    return None
                if not target_artifact.endswith(".py"):
                    evidence.append(f"Interface conformance skipped for non-Python artifact {target_artifact}.")
                    parsed_by_artifact[target_artifact] = (set(), set(), {})
                    return parsed_by_artifact[target_artifact]
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        tree = ast.parse(handle.read(), filename=path)
                except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                    errors.append(f"Interface {spec.get('id', 'unknown')} could not parse {target_artifact}: {exc}")
                    return None
                module_functions = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                module_assigns = self._module_assignment_names(tree)
                classes = {
                    node.name: node
                    for node in tree.body
                    if isinstance(node, ast.ClassDef)
                }
                parsed_by_artifact[target_artifact] = (module_functions, module_assigns, classes)
                return parsed_by_artifact[target_artifact]

            for symbol in spec.get("symbols", []) or []:
                if not isinstance(symbol, dict):
                    continue
                symbol_artifact = self._normalize_path(str(symbol.get("artifact") or artifact))
                parsed = parsed_interface_artifact(symbol_artifact)
                if parsed is None:
                    continue
                module_functions, module_assigns, classes = parsed
                kind = str(symbol.get("kind") or symbol.get("type") or "").lower()
                name = str(symbol.get("name") or "").strip()
                if not name:
                    continue
                if kind in {"function", "method"}:
                    if name not in module_functions:
                        errors.append(f"Interface {spec.get('id', 'unknown')} missing function `{name}` in {symbol_artifact}.")
                    continue
                if kind in {"class", "dataclass"}:
                    class_node = classes.get(name)
                    if class_node is None:
                        errors.append(f"Interface {spec.get('id', 'unknown')} missing class `{name}` in {symbol_artifact}.")
                        continue
                    class_methods = {
                        node.name
                        for node in class_node.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    }
                    for method in symbol.get("methods", []) or []:
                        method_name = self._method_name_from_signature(str(method))
                        if method_name and method_name not in class_methods:
                            errors.append(
                                f"Interface {spec.get('id', 'unknown')} missing method `{name}.{method_name}` in {symbol_artifact}."
                            )
                    continue
                if kind in {"constant", "constants"}:
                    names = [name]
                    names.extend(str(value).strip() for value in symbol.get("names", []) or [] if str(value).strip())
                    for constant_name in names:
                        if constant_name not in module_assigns:
                            errors.append(
                                f"Interface {spec.get('id', 'unknown')} missing constant `{constant_name}` in {symbol_artifact}."
                            )
                    continue
                if kind == "exception":
                    if name not in classes:
                        errors.append(f"Interface {spec.get('id', 'unknown')} missing exception `{name}` in {symbol_artifact}.")
            if len(errors) == spec_errors_before:
                checked = ", ".join(sorted(parsed_by_artifact)) or artifact
                evidence.append(f"Interface conformance passed for {spec.get('id', 'unknown')} in {checked}.")
        return errors, evidence

    @staticmethod
    def _method_name_from_signature(signature: str) -> str:
        return signature.split("(", 1)[0].strip().split(".", 1)[-1]

    @staticmethod
    def _module_assignment_names(tree: ast.Module) -> set[str]:
        names: set[str] = set()
        for node in tree.body:
            targets: List[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        return names

    def _run_python_import_check(self, artifact: str) -> tuple[str | None, str | None]:
        module = self._module_name_for_artifact(artifact)
        if not module:
            return None, f"Python import validation skipped for {artifact}: path is not importable as a module."
        command = [
            sys.executable,
            "-c",
            "import importlib, sys; importlib.import_module(sys.argv[1])",
            module,
        ]
        completed = self._run_python_command(command, timeout=20)
        if completed is None:
            return f"Python import validation timed out for {artifact}.", None
        output = self._command_output_tail(completed)
        if completed.returncode != 0:
            return f"Python import validation failed for {artifact} as {module}:\n{output}", None
        return None, f"Python import validation passed for {artifact} as {module}."

    def _run_package_compile(
        self,
        package_roots: List[str],
        python_artifacts: List[str],
    ) -> tuple[str | None, str | None]:
        targets = [root for root in package_roots if os.path.exists(os.path.join(self.workspace_dir, root))]
        if not targets:
            targets = sorted({os.path.dirname(path) or "." for path in python_artifacts})
        if not targets:
            return None, "Package compile skipped: no Python artifacts declared."
        completed = self._run_python_command([sys.executable, "-m", "compileall", "-q", *targets], timeout=60)
        if completed is None:
            return "Package compileall timed out.", None
        output = self._command_output_tail(completed)
        if completed.returncode != 0:
            return f"Package compileall failed:\n{output}", None
        return None, f"Package compileall passed for: {', '.join(targets)}."

    def _run_import_all(self, python_artifacts: List[str]) -> tuple[List[str], List[str]]:
        errors: List[str] = []
        evidence: List[str] = []
        modules = [
            module
            for artifact in python_artifacts
            if not self._is_python_test_artifact(artifact)
            for module in [self._module_name_for_artifact(artifact)]
            if module
        ]
        for module in sorted(set(modules)):
            completed = self._run_python_command(
                [
                    sys.executable,
                    "-c",
                    "import importlib, sys; importlib.import_module(sys.argv[1])",
                    module,
                ],
                timeout=20,
            )
            if completed is None:
                errors.append(f"Package import timed out for module {module}.")
                continue
            output = self._command_output_tail(completed)
            if completed.returncode != 0:
                errors.append(f"Package import failed for module {module}:\n{output}")
            else:
                evidence.append(f"Package import passed for module {module}.")
        return errors, evidence

    def _check_consumer_contracts(self, consumer_artifacts: Iterable[str]) -> tuple[List[str], List[str]]:
        consumers = [
            self._normalize_path(path)
            for path in consumer_artifacts
            if self._normalize_path(path).endswith(".py")
            and not self._is_python_test_artifact(self._normalize_path(path))
        ]
        if not consumers:
            return [], []

        signatures = self._workspace_symbol_signatures()
        if not signatures:
            return [], ["Consumer contract smoke skipped: no producer signatures available."]

        errors: List[str] = []
        checked = 0
        for artifact in consumers:
            path = os.path.join(self.workspace_dir, artifact)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            imports = self._import_aliases(tree, current_module=self._module_name_for_artifact(artifact))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = self._call_target(node.func, imports)
                if target is None:
                    continue
                module_name, symbol = target
                signature = signatures.get((module_name, symbol))
                if not signature:
                    continue
                if signature["artifact"] == artifact:
                    continue
                if self._call_uses_dynamic_arguments(node):
                    continue
                checked += 1
                missing = self._missing_required_arguments(node, signature)
                unexpected = self._unexpected_keyword_arguments(node, signature)
                if missing:
                    errors.append(
                        "Consumer contract smoke failed: "
                        f"{artifact} calls {module_name}.{symbol} without required argument(s) "
                        f"{', '.join(missing)} declared by {signature['artifact']}."
                    )
                if unexpected:
                    errors.append(
                        "Consumer contract smoke failed: "
                        f"{artifact} calls {module_name}.{symbol} with unexpected keyword argument(s) "
                        f"{', '.join(unexpected)} declared by {signature['artifact']}."
                    )
        if errors:
            return errors, []
        if checked:
            return [], [f"Consumer contract smoke passed for {checked} cross-module call(s)."]
        return [], ["Consumer contract smoke found no statically checkable cross-module calls."]

    def _workspace_symbol_signatures(self) -> Dict[tuple[str, str], Dict[str, Any]]:
        signatures: Dict[tuple[str, str], Dict[str, Any]] = {}
        for artifact in self._workspace_python_artifacts():
            if self._is_python_test_artifact(artifact):
                continue
            module_name = self._module_name_for_artifact(artifact)
            if not module_name:
                continue
            path = os.path.join(self.workspace_dir, artifact)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    signatures[(module_name, node.name)] = {
                        **self._signature_from_arguments(node.args, skip_first=False),
                        "artifact": artifact,
                    }
                elif isinstance(node, ast.ClassDef):
                    init = next(
                        (
                            child
                            for child in node.body
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__"
                        ),
                        None,
                    )
                    if init is not None:
                        signatures[(module_name, node.name)] = {
                            **self._signature_from_arguments(init.args, skip_first=True),
                            "artifact": artifact,
                        }
                    elif self._is_dataclass_node(node):
                        signatures[(module_name, node.name)] = {
                            **self._dataclass_signature(node),
                            "artifact": artifact,
                        }
        return signatures

    def _workspace_python_artifacts(self) -> List[str]:
        artifacts: List[str] = []
        for current_root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [
                name
                for name in dirs
                if name not in {".git", ".contractcoding", "__pycache__", ".venv", "node_modules"}
            ]
            for file_name in files:
                if not file_name.endswith(".py"):
                    continue
                rel_path = self._normalize_path(os.path.relpath(os.path.join(current_root, file_name), self.workspace_dir))
                if rel_path.startswith("tests/") or "/tests/" in f"/{rel_path}":
                    continue
                artifacts.append(rel_path)
        return sorted(artifacts)

    @staticmethod
    def _signature_from_arguments(args: ast.arguments, *, skip_first: bool) -> Dict[str, Any]:
        positional = [arg.arg for arg in [*args.posonlyargs, *args.args]]
        if skip_first and positional:
            positional = positional[1:]
        default_count = len(args.defaults)
        required = positional[: max(0, len(positional) - default_count)]
        keyword_only = [arg.arg for arg in args.kwonlyargs]
        required.extend(
            arg.arg
            for arg, default in zip(args.kwonlyargs, args.kw_defaults)
            if default is None
        )
        return {
            "required": required,
            "accepted": [*positional, *keyword_only],
            "positional": positional,
            "accepts_varargs": args.vararg is not None,
            "accepts_kwargs": args.kwarg is not None,
        }

    @staticmethod
    def _is_dataclass_node(node: ast.ClassDef) -> bool:
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
                return True
            if isinstance(decorator, ast.Attribute) and decorator.attr == "dataclass":
                return True
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if isinstance(func, ast.Name) and func.id == "dataclass":
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "dataclass":
                    return True
        return False

    @staticmethod
    def _dataclass_signature(node: ast.ClassDef) -> Dict[str, Any]:
        accepted: List[str] = []
        required: List[str] = []
        for child in node.body:
            field_name = ""
            has_default = True
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                field_name = child.target.id
                has_default = child.value is not None
            elif isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
                field_name = child.targets[0].id
                has_default = True
            if not field_name or field_name.startswith("_"):
                continue
            accepted.append(field_name)
            if not has_default:
                required.append(field_name)
        return {
            "required": required,
            "accepted": accepted,
            "positional": accepted,
            "accepts_varargs": False,
            "accepts_kwargs": False,
        }

    @classmethod
    def _import_aliases(cls, tree: ast.Module, *, current_module: str = "") -> Dict[str, tuple[str, str]]:
        aliases: Dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or node.level):
                module_name = cls._resolve_import_from_module(current_module, node.level, node.module or "")
                if not module_name:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    aliases[alias.asname or alias.name] = (module_name, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[-1]
                    aliases[local] = (alias.name, "")
        return aliases

    @staticmethod
    def _resolve_import_from_module(current_module: str, level: int, module: str) -> str:
        if level <= 0:
            return module
        current_parts = [part for part in current_module.split(".") if part]
        package_parts = current_parts[:-1]
        keep = max(0, len(package_parts) - (level - 1))
        base = package_parts[:keep]
        if module:
            base.extend(part for part in module.split(".") if part)
        return ".".join(base)

    @staticmethod
    def _call_target(func: ast.expr, imports: Dict[str, tuple[str, str]]) -> tuple[str, str] | None:
        if isinstance(func, ast.Name):
            target = imports.get(func.id)
            if target and target[1]:
                return target
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            target = imports.get(func.value.id)
            if target:
                module_name, imported_symbol = target
                if imported_symbol:
                    return (f"{module_name}.{imported_symbol}", func.attr)
                return (module_name, func.attr)
        return None

    @staticmethod
    def _call_uses_dynamic_arguments(node: ast.Call) -> bool:
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            return True
        return any(keyword.arg is None for keyword in node.keywords)

    @staticmethod
    def _missing_required_arguments(node: ast.Call, signature: Dict[str, Any]) -> List[str]:
        positional = list(signature.get("positional", []))
        provided_by_position = set(positional[: len(node.args)])
        provided_by_keyword = {keyword.arg for keyword in node.keywords if keyword.arg}
        return [
            name
            for name in signature.get("required", [])
            if name not in provided_by_position and name not in provided_by_keyword
        ]

    @staticmethod
    def _unexpected_keyword_arguments(node: ast.Call, signature: Dict[str, Any]) -> List[str]:
        if signature.get("accepts_kwargs"):
            return []
        accepted = set(signature.get("accepted", []))
        return [
            str(keyword.arg)
            for keyword in node.keywords
            if keyword.arg and keyword.arg not in accepted
        ]

    def _run_unittest_discover_if_required(
        self,
        python_artifacts: List[str],
        requires_tests: bool,
    ) -> tuple[str | None, str | None]:
        test_artifacts = [path for path in python_artifacts if self._is_python_test_artifact(path)]
        if not requires_tests and not test_artifacts:
            return None, "Unittest discovery skipped: no test artifacts declared."
        existing_tests = [path for path in test_artifacts if os.path.exists(os.path.join(self.workspace_dir, path))]
        if requires_tests and not existing_tests:
            return "Required tests are declared by contract but no test artifact exists.", None
        isolation_errors: List[str] = []
        for test_artifact in existing_tests:
            isolation_errors.extend(self._test_isolation_violations(test_artifact))
        if isolation_errors:
            return "\n".join(isolation_errors), None

        search_dir = "tests" if os.path.isdir(os.path.join(self.workspace_dir, "tests")) else "."
        completed = self._run_python_command(
            [sys.executable, "-m", "unittest", "discover", "-s", search_dir, "-p", "test*.py", "-v"],
            timeout=90,
        )
        if completed is None:
            return "Unittest discovery timed out.", None
        output = self._command_output_tail(completed, limit=12000)
        if completed.returncode != 0:
            return f"Unittest discovery failed:\n{output}", None
        summary_error = self._unittest_discovery_summary_error(output, requires_tests)
        if summary_error:
            return f"Unittest discovery failed: {summary_error}\n{output}", None
        command_error, command_evidence = self._run_discovered_project_tests(requires_tests)
        if command_error:
            return command_error, None
        evidence = f"Unittest discovery passed:\n{output}"
        if command_evidence:
            evidence += "\n" + command_evidence
        return None, evidence

    def _run_discovered_project_tests(self, requires_tests: bool) -> tuple[str | None, str | None]:
        mode = os.getenv("CONTRACTCODING_TEST_DISCOVERY", "auto")
        discovery = TestCommandDiscoverer(self.workspace_dir, mode=mode).discover(requires_tests=requires_tests)
        commands = [command for command in discovery.commands if command.name != "unittest"]
        evidence: List[str] = [f"Test discovery mode={discovery.mode}; commands={len(commands)}."]
        for test_command in commands:
            completed = self._run_python_command(test_command.command, timeout=120)
            if completed is None:
                return f"Discovered test command timed out: {' '.join(test_command.command)}", None
            output = self._command_output_tail(completed, limit=12000)
            if completed.returncode != 0:
                return f"Discovered test command failed ({test_command.name}):\n{output}", None
            evidence.append(f"Discovered test command passed ({test_command.name}):\n{output}")
        return None, "\n".join(evidence)

    @staticmethod
    def _unittest_discovery_summary_error(output: str, requires_tests: bool) -> str | None:
        if not requires_tests:
            return None
        ran_match = re.search(r"\bRan\s+(\d+)\s+tests?\b", output)
        if not ran_match:
            return "could not confirm that any executable tests were discovered."
        ran = int(ran_match.group(1))
        skipped = sum(int(value) for value in re.findall(r"skipped=(\d+)", output))
        if ran <= 0:
            return f"no executable tests ran (ran={ran}, skipped={skipped})."
        if skipped >= ran:
            return f"all discovered tests were skipped (ran={ran}, skipped={skipped})."
        return None

    def _check_product_behavior_contract(
        self,
        product_behavior: Dict[str, Any] | None,
        *,
        required_artifacts: List[str],
        python_artifacts: List[str],
    ) -> tuple[List[str], List[str]]:
        """Run behavior-level checks that are too product-shaped for generic unit tests.

        These checks are deliberately simple and deterministic. They do not try to
        prove the whole product correct; they catch false-done patterns such as a
        CLI module with no real ``python -m`` entrypoint or a named capability that
        exists only as a side module and never appears in integration tests.
        """

        contract = dict(product_behavior or {})
        if not contract:
            return [], []
        errors: List[str] = []
        evidence: List[str] = []
        capabilities = [
            str(value).strip()
            for value in contract.get("capabilities", [])
            if str(value).strip()
        ]
        if capabilities:
            evidence.append(f"Product behavior contract capabilities: {', '.join(capabilities)}.")

        command_errors, command_evidence = self._run_blackbox_behavior_commands(
            contract.get("blackbox_commands", [])
        )
        errors.extend(command_errors)
        evidence.extend(command_evidence)

        semantic_errors, semantic_evidence = self._check_semantic_behavior_requirements(
            contract.get("semantic_requirements", []),
            required_artifacts=required_artifacts,
            python_artifacts=python_artifacts,
        )
        errors.extend(semantic_errors)
        evidence.extend(semantic_evidence)
        return errors, evidence

    def _run_blackbox_behavior_commands(self, commands: Any) -> tuple[List[str], List[str]]:
        errors: List[str] = []
        evidence: List[str] = []
        if not isinstance(commands, list) or not commands:
            return errors, evidence
        for index, spec in enumerate(commands):
            if not isinstance(spec, dict):
                continue
            command_id = str(spec.get("id") or f"command_{index + 1}").strip()
            argv = self._normalize_blackbox_argv(spec.get("argv", []))
            if not argv:
                errors.append(f"Product behavior blackbox `{command_id}` has no executable argv.")
                continue
            if not self._is_allowed_blackbox_command(argv):
                errors.append(
                    f"Product behavior blackbox `{command_id}` uses unsupported command prefix: {' '.join(argv[:3])}."
                )
                continue
            timeout = max(1, min(int(spec.get("timeout_seconds", 15) or 15), 60))
            completed = self._run_python_command(argv, timeout=timeout)
            if completed is None:
                errors.append(f"Product behavior blackbox `{command_id}` timed out after {timeout}s: {' '.join(argv)}")
                continue
            expected = int(spec.get("expected_returncode", 0))
            output = self._command_output_tail(completed, limit=4000)
            if completed.returncode != expected:
                errors.append(
                    f"Product behavior blackbox `{command_id}` failed with return code {completed.returncode} "
                    f"(expected {expected}):\n{output}"
                )
                continue
            stdout = str(completed.stdout or "")
            stderr = str(completed.stderr or "")
            output_all = "\n".join(part for part in (stdout, stderr) if part)
            contains = [
                str(value)
                for value in spec.get("stdout_contains", [])
                if str(value)
            ]
            missing = [value for value in contains if value not in stdout]
            if missing:
                errors.append(
                    f"Product behavior blackbox `{command_id}` stdout missing expected text: {', '.join(missing)}."
                )
                continue
            any_terms = [
                str(value)
                for value in spec.get("stdout_contains_any", [])
                if str(value)
            ]
            if any_terms and not any(value.lower() in stdout.lower() for value in any_terms):
                errors.append(
                    f"Product behavior blackbox `{command_id}` stdout did not contain any of: {', '.join(any_terms)}."
                )
                continue
            if bool(spec.get("require_output", False)) and not output_all.strip():
                errors.append(f"Product behavior blackbox `{command_id}` produced no stdout/stderr output.")
                continue
            evidence.append(f"Product behavior blackbox `{command_id}` passed: {' '.join(argv)}.")
        return errors, evidence

    @staticmethod
    def _normalize_blackbox_argv(argv: Any) -> List[str]:
        if isinstance(argv, str):
            parts = argv.split()
        elif isinstance(argv, list):
            parts = [str(value) for value in argv]
        else:
            return []
        normalized: List[str] = []
        for index, part in enumerate(parts):
            value = str(part).strip()
            if not value:
                continue
            if index == 0 and value in {"python", "python3", "{python}", sys.executable}:
                value = sys.executable
            normalized.append(value)
        return normalized

    @staticmethod
    def _is_allowed_blackbox_command(argv: List[str]) -> bool:
        if not argv:
            return False
        executable = os.path.basename(argv[0])
        if argv[0] == sys.executable or executable.startswith("python"):
            return len(argv) >= 3 and argv[1] in {"-m", "-c"}
        return False

    def _check_semantic_behavior_requirements(
        self,
        requirements: Any,
        *,
        required_artifacts: List[str],
        python_artifacts: List[str],
    ) -> tuple[List[str], List[str]]:
        errors: List[str] = []
        evidence: List[str] = []
        if not isinstance(requirements, list) or not requirements:
            return errors, evidence
        test_artifacts = [
            artifact for artifact in python_artifacts if self._is_python_test_artifact(artifact)
        ]
        implementation_artifacts = [
            artifact for artifact in python_artifacts if not self._is_python_test_artifact(artifact)
        ]
        for index, raw in enumerate(requirements):
            if not isinstance(raw, dict):
                continue
            requirement_id = str(raw.get("id") or f"semantic_{index + 1}").strip()
            terms = [str(value).strip() for value in raw.get("required_terms", []) if str(value).strip()]
            any_terms = [str(value).strip() for value in raw.get("required_any_terms", []) if str(value).strip()]
            if raw.get("must_appear_in_tests"):
                artifacts = self._semantic_artifacts(raw.get("test_artifacts"), fallback=test_artifacts)
                missing = self._missing_terms_in_artifacts(terms, artifacts)
                if missing:
                    errors.append(
                        f"Semantic behavior `{requirement_id}` missing required term(s) in final tests: "
                        f"{', '.join(missing)}; artifacts={', '.join(artifacts)}."
                    )
                    continue
                if any_terms and not self._any_term_in_artifacts(any_terms, artifacts):
                    errors.append(
                        f"Semantic behavior `{requirement_id}` did not find any required test term "
                        f"({', '.join(any_terms)}) in artifacts={', '.join(artifacts)}."
                    )
                    continue
            if raw.get("must_appear_in_implementation"):
                artifacts = self._semantic_artifacts(raw.get("implementation_artifacts"), fallback=implementation_artifacts)
                missing = self._missing_terms_in_artifacts(terms, artifacts)
                if missing:
                    errors.append(
                        f"Semantic behavior `{requirement_id}` missing required term(s) in implementation: "
                        f"{', '.join(missing)}; artifacts={', '.join(artifacts)}."
                    )
                    continue
                if any_terms and not self._any_term_in_artifacts(any_terms, artifacts):
                    errors.append(
                        f"Semantic behavior `{requirement_id}` did not find any required implementation term "
                        f"({', '.join(any_terms)}) in artifacts={', '.join(artifacts)}."
                    )
                    continue
            consumer_terms = [
                str(value).strip()
                for value in raw.get("consumer_terms", [])
                if str(value).strip()
            ]
            if consumer_terms:
                artifacts = self._semantic_artifacts(raw.get("consumer_artifacts"), fallback=implementation_artifacts)
                if not self._any_term_in_artifacts(consumer_terms, artifacts):
                    errors.append(
                        f"Semantic behavior `{requirement_id}` is not wired through consumer artifact(s): "
                        f"expected one of {', '.join(consumer_terms)} in {', '.join(artifacts)}."
                    )
                    continue
            evidence.append(f"Semantic behavior `{requirement_id}` passed.")
        return errors, evidence

    def _semantic_artifacts(self, artifacts: Any, *, fallback: List[str]) -> List[str]:
        if isinstance(artifacts, list) and artifacts:
            candidates = [self._normalize_path(str(value)) for value in artifacts if str(value).strip()]
        else:
            candidates = list(fallback)
        return [
            artifact
            for artifact in self._dedupe(candidates)
            if os.path.exists(os.path.join(self.workspace_dir, artifact))
        ]

    def _missing_terms_in_artifacts(self, terms: List[str], artifacts: List[str]) -> List[str]:
        corpus = self._artifact_corpus(artifacts)
        return [term for term in terms if term.lower() not in corpus]

    def _any_term_in_artifacts(self, terms: List[str], artifacts: List[str]) -> bool:
        corpus = self._artifact_corpus(artifacts)
        return any(term.lower() in corpus for term in terms)

    def _artifact_corpus(self, artifacts: List[str]) -> str:
        chunks: List[str] = []
        for artifact in artifacts:
            content = self._read_text(os.path.join(self.workspace_dir, artifact))
            if content:
                chunks.append(content.lower())
        return "\n".join(chunks)

    def _run_python_command(self, command: List[str], timeout: int) -> subprocess.CompletedProcess | None:
        env = os.environ.copy()
        env["PYTHONPATH"] = self.workspace_dir + os.pathsep + env.get("PYTHONPATH", "")
        try:
            return subprocess.run(
                command,
                cwd=self.workspace_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None

    def _unexpected_source_files(self, required: List[str], allowed_extra: Iterable[str]) -> List[str]:
        required_set = set(required)
        allowed_set = {self._normalize_path(path) for path in allowed_extra}
        source_exts = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".go", ".rs", ".java", ".cpp", ".c", ".h"}
        unexpected: List[str] = []
        for current_root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [
                name
                for name in dirs
                if name not in {".git", "__pycache__", ".venv", "node_modules", ".contractcoding"}
            ]
            for file_name in files:
                rel_path = self._normalize_path(
                    os.path.relpath(os.path.join(current_root, file_name), self.workspace_dir)
                )
                if rel_path in required_set or rel_path in allowed_set:
                    continue
                if os.path.splitext(rel_path)[1] in source_exts:
                    unexpected.append(rel_path)
        return sorted(unexpected)

    def _placeholder_hits(self, artifacts: List[str]) -> List[str]:
        hits: List[str] = []
        pattern = re.compile(
            r"(^\s*pass\s*(#.*)?$|TODO|NotImplementedError|not implemented)",
            re.IGNORECASE | re.MULTILINE,
        )
        for artifact in artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    content = handle.read()
            except UnicodeDecodeError:
                continue
            if pattern.search(content):
                hits.append(artifact)
        return hits

    def _count_loc(self, artifacts: List[str]) -> int:
        total = 0
        for artifact in artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    total += sum(1 for line in handle if line.strip())
            except UnicodeDecodeError:
                continue
        return total

    def _read_text(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, UnicodeDecodeError):
            return None

    def _write_integration_report(
        self,
        item: WorkItem,
        result: InvariantResult,
        required: List[str],
        loc_total: int,
    ) -> None:
        target = item.target_artifacts[0] if item.target_artifacts else ".contractcoding/integration_report.json"
        path = os.path.join(self.workspace_dir, self._normalize_path(target))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "ok": result.ok,
            "errors": result.errors,
            "evidence": result.evidence,
            "required_artifact_count": len(required),
            "loc_non_empty": loc_total,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        result.evidence.append(f"Integration report written: {target}.")

    def _write_scope_report(
        self,
        item: WorkItem,
        result: InvariantResult,
        scope_id: str,
        required: List[str],
    ) -> None:
        target = item.target_artifacts[0] if item.target_artifacts else f".contractcoding/scope_reports/{scope_id}.json"
        path = os.path.join(self.workspace_dir, self._normalize_path(target))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "ok": result.ok,
            "scope_id": scope_id,
            "errors": result.errors,
            "evidence": result.evidence,
            "required_artifact_count": len(required),
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        result.evidence.append(f"Scope verification report written: {target}.")

    def _module_name_for_artifact(self, artifact: str) -> str:
        normalized = self._normalize_path(artifact)
        if not normalized.endswith(".py"):
            return ""
        parts = normalized[:-3].split("/")
        if not all(part.isidentifier() for part in parts):
            return ""
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            return ""
        return ".".join(parts)

    def _run_python_unittest(self, artifact: str) -> tuple[str | None, str | None]:
        isolation_errors = self._test_isolation_violations(artifact)
        if isolation_errors:
            return "\n".join(isolation_errors), None
        target = self._module_name_for_artifact(artifact) or artifact
        command = [sys.executable, "-m", "unittest", "-v", target]
        completed = self._run_python_command(command, timeout=30)
        if completed is None:
            return f"Unit test validation timed out for {artifact}.", None

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        output_tail = output[-1200:] if output else ""
        if completed.returncode != 0:
            return f"Unit test validation failed for {artifact}:\n{output_tail}", None
        summary_error = self._unittest_discovery_summary_error(output, requires_tests=True)
        if summary_error:
            return f"Unit test validation failed for {artifact}: {summary_error}\n{output_tail}", None
        return None, f"Unit test validation passed for {artifact}:\n{output_tail}"

    @staticmethod
    def _command_output_tail(completed: subprocess.CompletedProcess, limit: int = 1200) -> str:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        return output[-limit:] if output else ""

    @staticmethod
    def _is_integration_gate(item: WorkItem) -> bool:
        return item.kind == "eval" and item.verification_policy.get("system_gate") == "integration"

    @staticmethod
    def _is_scope_gate(item: WorkItem) -> bool:
        return item.kind == "eval" and item.verification_policy.get("system_gate") == "scope"

    @staticmethod
    def _is_runtime_side_effect(path: str) -> bool:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return (
            normalized == "agent.log"
            or normalized.endswith(".log")
            or normalized.startswith("__pycache__/")
            or normalized.startswith(".contractcoding/runs.sqlite")
            or normalized.startswith(".contractcoding/events")
            or normalized in {".contractcoding/contract.json", ".contractcoding/contract.md"}
        )

    @staticmethod
    def _is_python_test_artifact(artifact: str) -> bool:
        name = os.path.basename(artifact)
        return artifact.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{artifact}"
        )

    def _test_isolation_violations(self, artifact: str) -> List[str]:
        if not self._is_python_test_artifact(artifact):
            return []
        path = os.path.join(self.workspace_dir, self._normalize_path(artifact))
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except (OSError, UnicodeDecodeError):
            return []
        package_roots = self._workspace_package_roots()
        violations: List[str] = []
        for root in package_roots:
            escaped = re.escape(root)
            patterns = [
                rf"sys\.modules\s*\[\s*['\"]{escaped}['\"]\s*\]\s*=",
                rf"del\s+sys\.modules\s*\[\s*['\"]{escaped}['\"]\s*\]",
                rf"sys\.modules\.pop\s*\(\s*['\"]{escaped}['\"]",
                rf"sys\.modules\.update\s*\([^)]*['\"]{escaped}['\"]\s*:",
            ]
            if any(re.search(pattern, content, flags=re.MULTILINE | re.DOTALL) for pattern in patterns):
                violations.append(
                    "invalid_tests: generated tests must import the real package and must not replace, "
                    f"delete, or fake package root sys.modules['{root}'] in {artifact}."
                )
        return violations

    def _workspace_package_roots(self) -> List[str]:
        roots: List[str] = []
        try:
            names = os.listdir(self.workspace_dir)
        except OSError:
            return roots
        for name in names:
            if name.startswith(".") or name in {"tests", "__pycache__"}:
                continue
            path = os.path.join(self.workspace_dir, name)
            if not os.path.isdir(path) or not name.isidentifier():
                continue
            if os.path.exists(os.path.join(path, "__init__.py")):
                roots.append(name)
        return sorted(roots)

    @staticmethod
    def _is_textual_artifact(artifact: str) -> bool:
        return os.path.splitext(artifact)[1].lower() in {
            ".md",
            ".txt",
            ".json",
            ".yaml",
            ".yml",
            ".csv",
            ".tsv",
        }

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        for value in values:
            normalized = InvariantChecker._normalize_path(value)
            if normalized and normalized not in out:
                out.append(normalized)
        return out
