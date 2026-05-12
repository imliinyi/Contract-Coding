"""Slice and integration judges."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import json
import os
import py_compile
import subprocess
import sys
from typing import Any, Dict, Iterable, List

from ContractCoding.contract.spec import ContractSpec, FeatureSlice, WorkItem
from ContractCoding.quality.semantic import lint_canonical_type_ownership_for_artifacts, lint_contract_semantics


@dataclass
class GateResult:
    ok: bool
    evidence: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)


class SliceJudge:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def check(self, feature_slice: FeatureSlice, contract: ContractSpec | None = None) -> GateResult:
        evidence: List[str] = []
        diagnostics: List[Dict[str, Any]] = []
        for artifact in feature_slice.owner_artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                diagnostics.append(self._diag(feature_slice, "missing_artifact", artifact, f"{artifact} does not exist"))
                continue
            evidence.append(f"exists:{artifact}")
            if artifact.endswith(".py"):
                try:
                    py_compile.compile(path, doraise=True)
                    evidence.append(f"compile:{artifact}")
                except py_compile.PyCompileError as exc:
                    diagnostics.append(self._diag(feature_slice, "syntax_error", artifact, str(exc)))
            placeholder = self._placeholder(artifact)
            if placeholder:
                diagnostics.append(self._diag(feature_slice, "placeholder", artifact, placeholder))
        diagnostics.extend(self._check_interface_contract(feature_slice))
        smoke = self._run_slice_smoke(feature_slice)
        evidence.extend(smoke.evidence)
        diagnostics.extend(smoke.diagnostics)
        if contract is not None:
            canonical_evidence, canonical_diagnostics = lint_canonical_type_ownership_for_artifacts(
                self.workspace_dir,
                contract,
                feature_slice.owner_artifacts,
                slice_id=feature_slice.id,
            )
            evidence.extend(canonical_evidence)
            diagnostics.extend(canonical_diagnostics)
        budget = self._record_size_budget(feature_slice)
        evidence.extend(budget.evidence)
        diagnostics.extend(budget.diagnostics)
        for acceptance_ref in feature_slice.acceptance_refs:
            evidence.append(f"acceptance_ref:{acceptance_ref}")
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    def _check_interface_contract(self, feature_slice: FeatureSlice) -> List[Dict[str, Any]]:
        if not feature_slice.interface_contract:
            return []
        diagnostics: List[Dict[str, Any]] = []
        declared_owners = set(feature_slice.interface_contract.get("owner_artifacts", []) or [])
        missing_owners = [artifact for artifact in feature_slice.owner_artifacts if artifact not in declared_owners]
        if missing_owners:
            diagnostics.append(
                self._diag(
                    feature_slice,
                    "interface_contract_mismatch",
                    ",".join(missing_owners),
                    f"interface contract omits owner artifacts: {missing_owners}",
                )
            )
        for module in feature_slice.interface_contract.get("public_modules", []) or []:
            if not self._module_to_artifact(str(module), feature_slice.owner_artifacts):
                diagnostics.append(
                    self._diag(
                        feature_slice,
                        "interface_contract_mismatch",
                        str(module),
                        f"public module {module} is not backed by an owned artifact",
                    )
                )
        return diagnostics

    def _run_slice_smoke(self, feature_slice: FeatureSlice) -> GateResult:
        evidence: List[str] = []
        diagnostics: List[Dict[str, Any]] = []
        for smoke in feature_slice.slice_smoke:
            kind = str(smoke.get("kind", ""))
            if kind == "python_import":
                for module in smoke.get("modules", []) or []:
                    module_name = str(module)
                    result = self._run([sys.executable, "-c", f"import {module_name}; print('import-ok')"], timeout=int(smoke.get("timeout", 30) or 30))
                    if result.returncode == 0:
                        evidence.append(f"slice_smoke_import:{module_name}")
                    else:
                        diagnostics.append(
                            self._diag(
                                feature_slice,
                                "slice_smoke_import_failed",
                                module_name,
                                (result.stdout + result.stderr)[-1200:],
                            )
                        )
            elif kind == "command":
                argv = [sys.executable if str(part) == "{python}" else str(part) for part in smoke.get("argv", []) or []]
                if not argv:
                    continue
                result = self._run(argv, timeout=int(smoke.get("timeout", 30) or 30))
                if result.returncode == 0:
                    evidence.append(f"slice_smoke_command:{smoke.get('id', 'command')}")
                else:
                    diagnostics.append(
                        self._diag(
                            feature_slice,
                            "slice_smoke_command_failed",
                            str(smoke.get("id", "command")),
                            (result.stdout + result.stderr)[-1200:],
                        )
                    )
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    def _record_size_budget(self, feature_slice: FeatureSlice) -> GateResult:
        budget = dict((feature_slice.interface_contract or {}).get("size_budget", {}) or {})
        if not budget.get("enabled"):
            return GateResult(ok=True)
        minimum = int(budget.get("min_total_loc", 0) or 0)
        if minimum <= 0:
            return GateResult(ok=True)
        loc = self._count_loc(feature_slice.owner_artifacts)
        if loc < minimum:
            return GateResult(
                ok=True,
                evidence=[f"slice_size_budget_warning:{feature_slice.id}:{loc}/{minimum}"],
            )
        return GateResult(ok=True, evidence=[f"slice_size_budget:{feature_slice.id}:{loc}/{minimum}"])

    def _count_loc(self, artifacts: Iterable[str]) -> int:
        total = 0
        for artifact in artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except OSError:
                continue
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    total += 1
        return total

    def _placeholder(self, artifact: str) -> str:
        if not artifact.endswith((".py", ".md", ".txt")):
            return ""
        path = os.path.join(self.workspace_dir, artifact)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return ""
        lowered = text.lower()
        if "notimplementederror" in lowered or "todo" in lowered:
            return f"{artifact} contains placeholder text"
        if artifact.endswith(".py"):
            stripped = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
            if stripped in (["pass"], ["..."]):
                return f"{artifact} is pass-only"
        return ""

    def _run(self, argv: List[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(argv, cwd=self.workspace_dir, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(argv, 124, stdout=exc.stdout or "", stderr=(exc.stderr or "timeout"))

    @staticmethod
    def _module_to_artifact(module: str, owner_artifacts: Iterable[str]) -> str:
        candidates = [module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py"]
        for artifact in owner_artifacts:
            if artifact in candidates:
                return artifact
        return ""

    @staticmethod
    def _diag(feature_slice: FeatureSlice, code: str, artifact: str, message: str) -> Dict[str, Any]:
        return {
            "code": code,
            "artifact": artifact,
            "message": message,
            "slice_id": feature_slice.id,
            "kernel_invariant": (feature_slice.invariant_refs or ["slice_contract"])[0],
        }


class CapsuleJudge:
    """Validate that a producer team's interface capsule can be locked."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def check(self, contract: ContractSpec, item: WorkItem) -> GateResult:
        team_id = item.feature_team_id or item.slice_id.replace("capsule:", "", 1)
        capsule = next(
            (candidate for candidate in contract.interface_capsules if candidate.team_id == team_id),
            None,
        )
        if capsule is None:
            return GateResult(
                ok=False,
                diagnostics=[
                    self._diag("interface_capsule_missing", team_id, f"No interface capsule exists for team {team_id}")
                ],
            )
        diagnostics: List[Dict[str, Any]] = []
        evidence: List[str] = [
            f"interface_capsule:{capsule.id}",
            f"interface_capsule_status:{capsule.status}",
        ]
        if not capsule.owner_artifacts:
            diagnostics.append(self._diag("interface_capsule_missing_owner_artifacts", capsule.id, "capsule owns no production artifacts"))
        if not capsule.examples:
            diagnostics.append(self._diag("interface_capsule_missing_examples", capsule.id, "capsule has no executable examples"))
        python_owners = [artifact for artifact in capsule.owner_artifacts if artifact.endswith(".py") and not artifact.startswith("tests/")]
        if python_owners and not capsule.public_modules:
            diagnostics.append(self._diag("interface_capsule_missing_public_modules", capsule.id, "Python capsule has no public modules"))
        if not capsule.capabilities:
            diagnostics.append(self._diag("interface_capsule_missing_capabilities", capsule.id, "capsule has no declared capabilities"))
        if not capsule.compatibility:
            diagnostics.append(self._diag("interface_capsule_missing_compatibility", capsule.id, "capsule has no compatibility rule"))
        for module in capsule.public_modules[:12]:
            evidence.append(f"interface_capsule_public_module:{module}")
        for capability in capsule.capabilities[:12]:
            evidence.append(f"interface_capsule_capability:{capability}")
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    @staticmethod
    def _diag(code: str, artifact: str, message: str) -> Dict[str, Any]:
        return {
            "code": code,
            "artifact": artifact,
            "message": message,
            "kernel_invariant": "producer_consumer_shape",
        }


InterfaceJudge = CapsuleJudge


class IntegrationJudge:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def check(self, contract: ContractSpec) -> GateResult:
        evidence: List[str] = []
        diagnostics: List[Dict[str, str]] = []
        for artifact in contract.required_artifacts:
            if not os.path.exists(os.path.join(self.workspace_dir, artifact)):
                diagnostics.append(self._diag("artifact_coverage", "artifact_ownership", "missing_artifact", artifact, f"{artifact} does not exist"))
        python_artifacts = [artifact for artifact in contract.required_artifacts if artifact.endswith(".py")]
        for artifact in python_artifacts:
            try:
                py_compile.compile(os.path.join(self.workspace_dir, artifact), doraise=True)
                evidence.append(f"compile:{artifact}")
            except py_compile.PyCompileError as exc:
                diagnostics.append(self._diag("compile_import", "import_safe", "syntax_error", artifact, str(exc)))
        for root in self._package_roots(python_artifacts):
            result = self._run([sys.executable, "-c", f"import {root}; print('import-ok')"])
            if result.returncode == 0:
                evidence.append(f"import:{root}")
            else:
                diagnostics.append(self._diag("compile_import", "import_safe", "import_error", root, result.stderr[-800:]))
        if contract.test_artifacts and os.path.isdir(os.path.join(self.workspace_dir, "tests")):
            result = self._run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
            if result.returncode == 0:
                evidence.append("unittest:pass")
            else:
                diagnostics.append(
                    self._diag(
                        "declared_tests_pass",
                        "tests_compile_kernel_acceptance",
                        "test_failure",
                        "tests",
                        (result.stdout + result.stderr)[-2000:],
                    )
                )
            function_tests = self._run_python_test_functions(contract.test_artifacts)
            evidence.extend(function_tests.evidence)
            diagnostics.extend(function_tests.diagnostics)
            if (
                "Ran 0 tests" in (result.stdout + result.stderr)
                and not function_tests.diagnostics
                and not any(item.startswith("python_test_functions:") for item in function_tests.evidence)
            ):
                diagnostics.append(
                    self._diag(
                        "declared_tests_pass",
                        "tests_compile_kernel_acceptance",
                        "no_executable_tests",
                        "tests",
                        "unittest discovered 0 tests and no executable test_* functions were found",
                    )
                )
        for public_path in contract.product_kernel.public_paths:
            if public_path.get("kind") != "cli":
                continue
            argv = [sys.executable if part == "{python}" else str(part) for part in public_path.get("argv", [])]
            result = self._run(argv)
            if result.returncode == 0:
                evidence.append(f"cli:{public_path.get('id')}")
            else:
                diagnostics.append(
                    self._diag(
                        str(public_path.get("id") or "public_path"),
                        "producer_consumer_shape",
                        "cli_failure",
                        str(public_path.get("id")),
                        result.stderr[-800:],
                    )
                )
        behavior = self._run_public_behavior_flows(contract)
        evidence.extend(behavior.evidence)
        diagnostics.extend(behavior.diagnostics)
        semantic = self._check_kernel_semantics(contract)
        evidence.extend(semantic.evidence)
        diagnostics.extend(semantic.diagnostics)
        mock_lifecycle = self._check_unresolved_mocks(contract)
        evidence.extend(mock_lifecycle.evidence)
        diagnostics.extend(mock_lifecycle.diagnostics)
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    def _run_public_behavior_flows(self, contract: ContractSpec) -> GateResult:
        evidence: List[str] = []
        diagnostics: List[Dict[str, Any]] = []
        for flow in contract.product_kernel.flows:
            if flow.get("kind") != "python_behavior_probe":
                continue
            flow_id = str(flow.get("id") or "public_behavior_flow")
            code = str(flow.get("code") or "")
            if not code.strip():
                diagnostics.append(
                    self._diag(
                        "public_behavior_flow",
                        "public_behavior_examples",
                        "public_flow_missing_code",
                        flow_id,
                        f"{flow_id} has no executable code",
                    )
                )
                continue
            result = self._run([sys.executable, "-c", code])
            if result.returncode == 0:
                evidence.append(f"public_flow:{flow_id}:pass")
            else:
                diagnostics.append(
                    self._diag(
                        "public_behavior_flow",
                        "public_behavior_examples",
                        "public_flow_failed",
                        flow_id,
                        (result.stdout + result.stderr)[-2000:],
                    )
                )
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    def _check_kernel_semantics(self, contract: ContractSpec) -> GateResult:
        evidence: List[str] = []
        diagnostics: List[Dict[str, Any]] = []
        for invariant in contract.product_kernel.semantic_invariants:
            if invariant.get("kind") != "loc_budget":
                continue
            minimum = int(invariant.get("min_total_loc", 0) or 0)
            loc = self._count_loc(contract.required_artifacts)
            if loc < minimum:
                evidence.append(f"kernel_loc_budget_warning:{loc}/{minimum}")
            else:
                evidence.append(f"kernel_loc_budget:{loc}/{minimum}")
        lint_evidence, lint_diagnostics = lint_contract_semantics(self.workspace_dir, contract)
        evidence.extend(lint_evidence)
        diagnostics.extend(lint_diagnostics)
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    def _check_unresolved_mocks(self, contract: ContractSpec) -> GateResult:
        diagnostics: List[Dict[str, Any]] = []
        evidence: List[str] = []
        production_artifacts = [
            artifact
            for artifact in contract.required_artifacts
            if artifact.endswith((".py", ".json", ".yaml", ".yml", ".toml", ".md"))
            and artifact not in contract.test_artifacts
        ]
        for artifact in production_artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                continue
            if "CONTRACTCODING_MOCK" in text or '"mock_id"' in text:
                diagnostics.append(
                    self._diag(
                        "controlled_mock_lifecycle",
                        "no_unresolved_mocks",
                        "unresolved_mock",
                        artifact,
                        f"{artifact} contains marked temporary mock metadata at final integration",
                    )
                )
        if not diagnostics:
            evidence.append("controlled_mock_lifecycle:no_unresolved_mocks")
        return GateResult(ok=not diagnostics, evidence=evidence, diagnostics=diagnostics)

    def _count_loc(self, artifacts: Iterable[str]) -> int:
        total = 0
        for artifact in artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except OSError:
                continue
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    total += 1
        return total

    def _run(self, argv: List[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(argv, cwd=self.workspace_dir, text=True, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(argv, 124, stdout=exc.stdout or "", stderr=(exc.stderr or "timeout"))

    def _run_python_test_functions(self, test_artifacts: Iterable[str]) -> GateResult:
        tests = [artifact for artifact in test_artifacts if artifact.endswith(".py")]
        if not tests:
            return GateResult(ok=True)
        code = (
            "import importlib.util, inspect, json, pathlib, sys, traceback\n"
            f"TEST_PATHS = {json.dumps(tests)}\n"
            "sys.path.insert(0, '.')\n"
            "total = 0\n"
            "failures = []\n"
            "for raw_path in TEST_PATHS:\n"
            "    path = pathlib.Path(raw_path)\n"
            "    if not path.exists():\n"
            "        continue\n"
            "    try:\n"
            "        spec = importlib.util.spec_from_file_location(path.stem, path)\n"
            "        module = importlib.util.module_from_spec(spec)\n"
            "        assert spec.loader is not None\n"
            "        spec.loader.exec_module(module)\n"
            "    except Exception as exc:\n"
            "        failures.append({'path': str(path), 'test': '<import>', 'error': exc.__class__.__name__, 'message': str(exc)})\n"
            "        continue\n"
            "    for name, fn in sorted(vars(module).items()):\n"
            "        if not (name.startswith('test_') and callable(fn)):\n"
            "            continue\n"
            "        total += 1\n"
            "        try:\n"
            "            signature = inspect.signature(fn)\n"
            "            required = [p for p in signature.parameters.values() if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]\n"
            "            if required:\n"
            "                raise TypeError('test function requires unsupported fixtures: ' + ', '.join(p.name for p in required))\n"
            "            fn()\n"
            "        except Exception as exc:\n"
            "            failures.append({'path': str(path), 'test': name, 'error': exc.__class__.__name__, 'message': str(exc), 'traceback': traceback.format_exc(limit=6)})\n"
            "print(json.dumps({'total': total, 'failed': len(failures), 'failures': failures[:20]}, sort_keys=True))\n"
            "sys.exit(1 if failures else 0)\n"
        )
        result = self._run([sys.executable, "-c", code])
        try:
            payload = json.loads((result.stdout or "{}").strip().splitlines()[-1])
        except Exception:
            payload = {"total": 0, "failed": 1, "failures": [{"message": (result.stdout + result.stderr)[-1200:]}]}
        total = int(payload.get("total", 0) or 0)
        failed = int(payload.get("failed", 0) or 0)
        if failed:
            return GateResult(
                ok=False,
                diagnostics=[
                    self._diag(
                        "declared_tests_pass",
                        "tests_compile_kernel_acceptance",
                        "python_test_function_failure",
                        str((failure or {}).get("path", "tests")),
                        f"{(failure or {}).get('test', '<unknown>')}: {(failure or {}).get('error', 'Error')}: {(failure or {}).get('message', '')}",
                    )
                    for failure in payload.get("failures", [])[:10]
                ],
            )
        if total:
            return GateResult(ok=True, evidence=[f"python_test_functions:pass:{total}"])
        return GateResult(ok=True)

    @staticmethod
    def _package_roots(python_artifacts: Iterable[str]) -> List[str]:
        roots: List[str] = []
        for artifact in python_artifacts:
            parts = artifact.split("/")
            if len(parts) > 1 and parts[0].isidentifier() and parts[0] != "tests" and parts[0] not in roots:
                roots.append(parts[0])
        return roots

    @staticmethod
    def _diag(acceptance_id: str, invariant: str, code: str, artifact: str, message: str) -> Dict[str, Any]:
        return {
            "acceptance_id": acceptance_id,
            "kernel_invariant": invariant,
            "code": code,
            "artifact": artifact,
            "message": message,
        }


class RepairJudge:
    """Validate central repair transactions before their patch is promoted."""

    def __init__(self, team_workspace_dir: str, main_workspace_dir: str):
        self.team_workspace_dir = os.path.abspath(team_workspace_dir)
        self.main_workspace_dir = os.path.abspath(main_workspace_dir)

    def check(self, contract: ContractSpec, item: WorkItem) -> GateResult:
        transaction = next(
            (candidate for candidate in contract.repair_transactions if candidate.id == item.repair_transaction_id),
            None,
        )
        if transaction is None:
            return GateResult(
                ok=False,
                diagnostics=[
                    {
                        "code": "repair_transaction_missing",
                        "artifact": item.slice_id,
                        "message": f"{item.repair_transaction_id} is not present in contract",
                        "kernel_invariant": "repair_exact_validation_required",
                    }
                ],
            )

        evidence: List[str] = []
        diagnostics: List[Dict[str, Any]] = []
        for locked in transaction.locked_tests:
            main_path = os.path.join(self.main_workspace_dir, locked)
            team_path = os.path.join(self.team_workspace_dir, locked)
            if os.path.exists(main_path) and os.path.exists(team_path):
                try:
                    with open(main_path, "rb") as left, open(team_path, "rb") as right:
                        if left.read() != right.read():
                            diagnostics.append(self._diag("locked_test_modified", locked, f"{locked} changed during repair"))
                except OSError as exc:
                    diagnostics.append(self._diag("locked_test_compare_failed", locked, str(exc)))

        commands = transaction.validation_commands or self._default_validation_commands(contract)
        command_records: List[Dict[str, Any]] = []
        for command in commands:
            argv = [sys.executable if str(part) == "{python}" else str(part) for part in command]
            result = self._run(argv, timeout=90)
            command_records.append(
                {
                    "argv": argv,
                    "returncode": result.returncode,
                    "tail": (result.stdout + result.stderr)[-1200:],
                }
            )
            if result.returncode == 0:
                evidence.append(f"repair_validation:{' '.join(argv[1:])}:pass")
            else:
                diagnostics.append(self._diag("repair_validation_failed", " ".join(argv), (result.stdout + result.stderr)[-2000:]))
                break
        if not diagnostics and transaction.locked_tests:
            function_tests = IntegrationJudge(self.team_workspace_dir)._run_python_test_functions(transaction.locked_tests)
            evidence.extend(function_tests.evidence)
            diagnostics.extend(function_tests.diagnostics)
        transaction.last_validation = {
            "ok": not diagnostics,
            "commands": command_records,
            "locked_tests": list(transaction.locked_tests),
        }
        if diagnostics:
            transaction.evidence.append(f"repair validation failed for {item.id}")
            return GateResult(ok=False, evidence=evidence, diagnostics=diagnostics)
        transaction.status = "PATCH_VALIDATED"
        transaction.evidence.append(f"repair validation passed for {item.id}")
        return GateResult(ok=True, evidence=evidence, diagnostics=diagnostics)

    def _default_validation_commands(self, contract: ContractSpec) -> List[List[str]]:
        commands: List[List[str]] = []
        for test in contract.test_artifacts:
            module = self._test_module(test)
            if module:
                commands.append(["{python}", "-m", "unittest", module, "-v"])
        if os.path.isdir(os.path.join(self.team_workspace_dir, "tests")):
            commands.append(["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"])
        return commands or [["{python}", "-m", "compileall", "."]]

    @staticmethod
    def _test_module(path: str) -> str:
        normalized = path.replace("\\", "/")
        if not normalized.endswith(".py"):
            return ""
        stem = normalized[:-3]
        parts = [part for part in stem.split("/") if part]
        if all(part.isidentifier() for part in parts):
            return ".".join(parts)
        return ""

    def _run(self, argv: List[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(argv, cwd=self.team_workspace_dir, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(argv, 124, stdout=exc.stdout or "", stderr=(exc.stderr or "timeout"))

    @staticmethod
    def _diag(code: str, artifact: str, message: str) -> Dict[str, Any]:
        return {
            "code": code,
            "artifact": artifact,
            "message": message,
            "kernel_invariant": "repair_exact_validation_required",
        }
