"""Static checks for scope-local generated tests."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Iterable, List, Set


@dataclass
class TestStrataAudit:
    errors: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class TestStrataAuditor:
    """Reject scope-local tests that require unavailable dependency scopes.

    Scope gates should validate one team in its own workspace. Cross-scope
    imports are acceptable only when the dependency module is actually present
    in that workspace. Otherwise the test belongs in a later integration layer
    or the gate must wait for the dependency scope to promote first.
    """

    KNOWN_SCOPES = {"package", "domain", "core", "ai", "io", "interface"}

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def audit_scope_tests(
        self,
        *,
        scope_id: str,
        test_artifacts: Iterable[str],
        scope_artifacts: Iterable[str],
        dependency_scope_ids: Iterable[str] = (),
    ) -> TestStrataAudit:
        errors: List[str] = []
        evidence: List[str] = []
        package_roots = self._package_roots([*scope_artifacts, *test_artifacts])
        dependency_scopes = {str(scope).strip() for scope in dependency_scope_ids if str(scope).strip()}
        for artifact in test_artifacts:
            path = os.path.join(self.workspace_dir, artifact)
            if not os.path.exists(path):
                continue
            modules = self._referenced_modules(path)
            for module in sorted(modules):
                ref_scope = self._module_scope(module, package_roots)
                if not ref_scope or ref_scope in {scope_id, "package"}:
                    continue
                if self._module_available(module):
                    evidence.append(f"Scope-local test reference available: {module}.")
                    continue
                allowed_later = ref_scope in dependency_scopes
                layer = "dependency scope" if allowed_later else "cross-scope"
                errors.append(
                    f"Scope-local test {artifact} references unavailable {layer} module "
                    f"`{module}` from scope `{ref_scope}` while validating `{scope_id}`. "
                    "Move this assertion to integration/final tests, patch through the current scope module, "
                    "or wait until the dependency scope is promoted."
                )
        if not errors:
            evidence.append("Scope-local test strata audit passed.")
        return TestStrataAudit(errors=errors, evidence=evidence)

    def _referenced_modules(self, path: str) -> Set[str]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
        except (OSError, SyntaxError):
            return set()
        modules: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Call):
                patch_target = self._mock_patch_target(node)
                if patch_target:
                    modules.add(patch_target)
        return modules

    @staticmethod
    def _mock_patch_target(node: ast.Call) -> str:
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
            value = func.value
            if isinstance(value, ast.Attribute):
                name = f"{value.attr}.{name}"
            elif isinstance(value, ast.Name):
                name = f"{value.id}.{name}"
        if name not in {"mock.patch", "patch"}:
            return ""
        if not node.args:
            return ""
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return ""

    def _module_available(self, module: str) -> bool:
        parts = [part for part in str(module).split(".") if part]
        while len(parts) > 1:
            module_path = os.path.join(self.workspace_dir, *parts)
            if os.path.isdir(module_path) or os.path.exists(module_path + ".py"):
                return True
            parts.pop()
        return False

    def _module_scope(self, module: str, package_roots: Set[str]) -> str:
        pieces = [piece for piece in str(module).split(".") if piece]
        if not pieces or pieces[0] not in package_roots:
            return ""
        for piece in pieces[1:]:
            if piece in self.KNOWN_SCOPES:
                return piece
        if pieces[-1] in {"cli", "__main__", "main"}:
            return "interface"
        return "package"

    @staticmethod
    def _package_roots(paths: Iterable[str]) -> Set[str]:
        roots: Set[str] = set()
        for path in paths:
            normalized = str(path or "").replace("\\", "/").strip("/")
            if not normalized.endswith(".py") or normalized.startswith("tests/") or "/tests/" in normalized:
                continue
            pieces = [piece for piece in normalized.split("/") if piece]
            if len(pieces) > 1:
                roots.add(pieces[0])
        return roots
