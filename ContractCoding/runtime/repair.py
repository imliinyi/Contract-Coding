"""Transactional repair helpers for guarded tool writes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
import py_compile
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

from ContractCoding.llm.base import ToolIntent
from ContractCoding.tools.intent_executor import ToolExecutionResult


WRITE_TOOLS = {"write_file", "replace_file", "create_file", "update_file_lines", "add_code", "replace_symbol"}


@dataclass(frozen=True)
class RepairSession:
    run_id: str = ""
    scope_id: str = ""
    work_item_id: str = ""
    diagnostic_fingerprint: str = ""
    allowed_artifacts: List[str] = field(default_factory=list)
    repair_mode: str = "line_patch"
    status: str = "ACTIVE"


@dataclass(frozen=True)
class RepairAttempt:
    session_id: str
    tool_intents: List[Dict[str, Any]] = field(default_factory=list)
    touched_artifacts: List[str] = field(default_factory=list)
    validation_result: Dict[str, Any] = field(default_factory=dict)
    rolled_back: bool = False


@dataclass(frozen=True)
class PatchGuardResult:
    ok: bool
    status: str
    touched_artifacts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    next_read_ranges: Dict[str, List[int]] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "touched_artifacts": list(self.touched_artifacts),
            "errors": list(self.errors),
            "evidence": list(self.evidence),
        }
        if self.next_read_ranges:
            payload["next_suggested_read_ranges"] = dict(self.next_read_ranges)
        return payload


class PatchGuard:
    """Validate and roll back write intents during repair sessions."""

    def __init__(
        self,
        workspace_dir: str,
        *,
        allowed_artifacts: Optional[List[str]] = None,
        diagnostic_text: str = "",
        timeout: int = 25,
        validate_imports: bool = True,
    ):
        self.workspace_dir = os.path.abspath(workspace_dir or ".")
        self.allowed_artifacts = [self._normalize(path) for path in allowed_artifacts or [] if str(path).strip()]
        self.diagnostic_text = diagnostic_text or ""
        self.timeout = max(5, int(timeout or 25))
        self.validate_imports = bool(validate_imports)
        self._snapshots: Dict[int, Dict[str, Optional[str]]] = {}

    def before_tool(self, intent: ToolIntent) -> None:
        path = self._path_for_intent(intent)
        if intent.name not in WRITE_TOOLS or not path:
            return
        artifact = self._normalize(path)
        if self.allowed_artifacts and artifact not in self.allowed_artifacts:
            return
        abs_path = self._resolve(artifact)
        snapshot: Dict[str, Optional[str]] = {}
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8") as handle:
                snapshot[artifact] = handle.read()
        else:
            snapshot[artifact] = None
        self._snapshots[id(intent)] = snapshot

    def after_tool(self, intent: ToolIntent, result: ToolExecutionResult) -> ToolExecutionResult:
        snapshot = self._snapshots.pop(id(intent), {})
        if intent.name not in WRITE_TOOLS or not snapshot or not result.allowed:
            return result
        touched = self._changed_artifacts(snapshot)
        if not touched:
            return result
        guard_result = self._validate(touched)
        if guard_result.ok:
            output = self._append_guard_output(result.output, guard_result)
            return replace(
                result,
                output=output,
                validation_status=guard_result.status,
                touched_files=guard_result.touched_artifacts,
                validation_errors=[],
                rolled_back=False,
            )

        self._rollback(snapshot)
        rolled_back = PatchGuardResult(
            ok=False,
            status="rolled_back",
            touched_artifacts=guard_result.touched_artifacts,
            errors=guard_result.errors,
            evidence=guard_result.evidence,
            next_read_ranges=guard_result.next_read_ranges,
        )
        output = self._append_guard_output(result.output, rolled_back)
        return replace(
            result,
            output=output,
            validation_status=rolled_back.status,
            touched_files=rolled_back.touched_artifacts,
            validation_errors=rolled_back.errors,
            rolled_back=True,
        )

    def _validate(self, artifacts: List[str]) -> PatchGuardResult:
        errors: List[str] = []
        evidence: List[str] = []
        ranges: Dict[str, List[int]] = {}
        python_artifacts = [artifact for artifact in artifacts if artifact.endswith(".py")]
        for artifact in python_artifacts:
            compile_error = self._compile_python(artifact)
            if compile_error:
                errors.append(compile_error)
                ranges.update(self._read_ranges_for_error(artifact, compile_error))
                continue
            evidence.append(f"py_compile passed for {artifact}")
            if self.validate_imports and not self._is_test_artifact(artifact):
                import_error = self._import_module_for_artifact(artifact)
                if import_error:
                    errors.append(import_error)
                    ranges.update(self._read_ranges_for_error(artifact, import_error))
                else:
                    evidence.append(f"targeted import passed for {artifact}")
            placeholder_error = self._placeholder_error(artifact)
            if placeholder_error:
                errors.append(placeholder_error)
        target = self._targeted_unittest()
        pending_global_validation = False
        if not errors:
            test_error = self._run_targeted_test(target)
            if test_error:
                if self._is_broad_final_target(target):
                    pending_global_validation = True
                    evidence.append(
                        "broad final validation still has failures; local patch kept for bundle-level convergence"
                    )
                else:
                    errors.append(test_error)
                    for artifact in artifacts:
                        ranges.setdefault(artifact, self._fallback_range(artifact, 1))
            elif target:
                evidence.append("targeted failing test passed")
        status = "applied_pending_global_validation" if pending_global_validation and not errors else ("applied" if not errors else "rejected")
        return PatchGuardResult(
            ok=not errors,
            status=status,
            touched_artifacts=artifacts,
            errors=errors,
            evidence=evidence,
            next_read_ranges=ranges,
        )

    def _compile_python(self, artifact: str) -> str:
        try:
            py_compile.compile(self._resolve(artifact), doraise=True)
            return ""
        except py_compile.PyCompileError as exc:
            return f"py_compile failed for {artifact}: {exc.msg}"
        except Exception as exc:
            return f"py_compile failed for {artifact}: {exc}"

    def _import_module_for_artifact(self, artifact: str) -> str:
        module = self._module_name_for_artifact(artifact)
        if not module:
            return ""
        code = (
            "import importlib, sys; "
            f"sys.path.insert(0, {self.workspace_dir!r}); "
            f"importlib.import_module({module!r})"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=min(self.timeout, 12),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"targeted import timed out for {artifact}: {module}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return f"targeted import failed for {artifact} ({module}): {detail[:1600]}"
        return ""

    def _run_targeted_test(self, target: str = "") -> str:
        target = target or self._targeted_unittest()
        if not target:
            return ""
        if target == "__discover_tests__":
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
            display_target = "unittest discover -s tests"
        else:
            command = [sys.executable, "-m", "unittest", target]
            display_target = target
        try:
            result = subprocess.run(
                command,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"targeted unittest timed out: {display_target}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return f"targeted unittest failed for {display_target}: {detail[:2400]}"
        return ""

    def _is_broad_final_target(self, target: str) -> bool:
        if target != "__discover_tests__":
            return False
        lower = self.diagnostic_text.lower()
        return "final" in lower or "integration" in lower or "unittest discovery failed" in lower

    def _placeholder_error(self, artifact: str) -> str:
        if "placeholder" not in self.diagnostic_text.lower():
            return ""
        try:
            with open(self._resolve(artifact), "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            return ""
        patterns = (
            r"^\s*pass\s*(#.*)?$",
            r"TODO",
            r"placeholder",
            r"NotImplementedError",
        )
        for pattern in patterns:
            if re.search(pattern, content, flags=re.IGNORECASE | re.MULTILINE):
                return f"placeholder scan failed for {artifact}: pattern {pattern!r}"
        return ""

    def _targeted_unittest(self) -> str:
        if not self._should_run_targeted_test():
            return ""
        text = self.diagnostic_text
        for pattern in (
            r"\b(tests\.[A-Za-z0-9_.]+\.test_[A-Za-z0-9_]+)\b",
            r"\(([A-Za-z0-9_.]+\.test_[A-Za-z0-9_]+)\)",
        ):
            match = re.search(pattern, text)
            if match:
                return self._normalize_unittest_target(match.group(1))
        artifact_match = re.search(r"\b(tests/[A-Za-z0-9_./-]+\.py)\b", text)
        if artifact_match:
            return self._module_name_for_artifact(artifact_match.group(1))
        failing = re.search(r"failing_test:\s*([A-Za-z0-9_.]+)", text)
        if failing and "." in failing.group(1):
            return self._normalize_unittest_target(failing.group(1))
        lower = text.lower()
        if ("final" in lower or "integration" in lower) and os.path.isdir(self._resolve("tests")):
            return "__discover_tests__"
        return ""

    def _normalize_unittest_target(self, target: str) -> str:
        normalized = str(target or "").strip()
        module = normalized.split(".", 1)[0]
        if module.startswith("test_") and os.path.exists(self._resolve(f"tests/{module}.py")):
            return f"tests.{normalized}"
        return normalized

    def _should_run_targeted_test(self) -> bool:
        lower = self.diagnostic_text.lower()
        return bool(
            "failing_test:" in lower
            or "unit test validation failed" in lower
            or "unittest" in lower
            or "final" in lower
            or "integration" in lower
        )

    def _changed_artifacts(self, snapshot: Dict[str, Optional[str]]) -> List[str]:
        changed: List[str] = []
        for artifact, before in snapshot.items():
            abs_path = self._resolve(artifact)
            after: Optional[str]
            if os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8") as handle:
                    after = handle.read()
            else:
                after = None
            if before != after:
                changed.append(artifact)
        return changed

    def _rollback(self, snapshot: Dict[str, Optional[str]]) -> None:
        for artifact, content in snapshot.items():
            abs_path = self._resolve(artifact)
            if content is None:
                try:
                    os.remove(abs_path)
                except FileNotFoundError:
                    pass
                continue
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as handle:
                handle.write(content)

    @staticmethod
    def _append_guard_output(output: str, result: PatchGuardResult) -> str:
        payload = json.dumps({"contractcoding_repair_validation": result.to_record()}, ensure_ascii=False, indent=2)
        return f"{output}\n\n{payload}"

    def _read_ranges_for_error(self, artifact: str, error: str) -> Dict[str, List[int]]:
        match = re.search(r"line\s+(\d+)", error, flags=re.IGNORECASE)
        line = int(match.group(1)) if match else 1
        return {artifact: self._fallback_range(artifact, line)}

    def _fallback_range(self, artifact: str, line: int) -> List[int]:
        try:
            with open(self._resolve(artifact), "r", encoding="utf-8") as handle:
                total = len(handle.readlines())
        except OSError:
            total = max(line + 80, 120)
        return [max(1, line - 40), min(max(total, line), line + 80)]

    def _module_name_for_artifact(self, artifact: str) -> str:
        normalized = self._normalize(artifact)
        if not normalized.endswith(".py") or self._is_test_artifact(normalized):
            if self._is_test_artifact(normalized):
                return normalized[:-3].replace("/", ".")
            return ""
        module = normalized[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        return module

    @staticmethod
    def _is_test_artifact(artifact: str) -> bool:
        normalized = artifact.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    def _path_for_intent(self, intent: ToolIntent) -> str:
        return str(
            intent.arguments.get("path")
            or intent.arguments.get("file_path")
            or intent.arguments.get("target_path")
            or ""
        )

    def _resolve(self, path: str) -> str:
        normalized = self._normalize(path)
        if os.path.isabs(normalized):
            abs_path = os.path.abspath(normalized)
        else:
            abs_path = os.path.abspath(os.path.join(self.workspace_dir, normalized))
        if abs_path != self.workspace_dir and not abs_path.startswith(self.workspace_dir + os.sep):
            raise ValueError(f"Path {path!r} resolves outside workspace.")
        return abs_path

    @staticmethod
    def _normalize(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/").strip("/")
        return normalized[2:] if normalized.startswith("./") else normalized
