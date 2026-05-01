"""Structured diagnostics for gate and repair loops."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import re
from typing import Any, Dict, Iterable, List, Optional


_MAX_EXCERPT = 2400


@dataclass(frozen=True)
class DiagnosticRecord:
    gate_id: str
    scope_id: str
    failure_kind: str
    failed_command: str = ""
    failing_test: str = ""
    traceback_excerpt: str = ""
    expected_actual: str = ""
    suspected_symbols: List[str] = field(default_factory=list)
    affected_artifacts: List[str] = field(default_factory=list)
    test_artifacts: List[str] = field(default_factory=list)
    suspected_implementation_artifacts: List[str] = field(default_factory=list)
    suspected_scopes: List[str] = field(default_factory=list)
    external_artifacts: List[str] = field(default_factory=list)
    recovery_action: str = ""
    primary_scope: str = ""
    fallback_scopes: List[str] = field(default_factory=list)
    repair_instruction: str = ""

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "DiagnosticRecord":
        return cls(
            gate_id=str(payload.get("gate_id", "")),
            scope_id=str(payload.get("scope_id", "")),
            failure_kind=str(payload.get("failure_kind", "unknown")),
            failed_command=str(payload.get("failed_command", "")),
            failing_test=str(payload.get("failing_test", "")),
            traceback_excerpt=str(payload.get("traceback_excerpt", "")),
            expected_actual=str(payload.get("expected_actual", "")),
            suspected_symbols=[str(value) for value in payload.get("suspected_symbols", []) if str(value)],
            affected_artifacts=[str(value) for value in payload.get("affected_artifacts", []) if str(value)],
            test_artifacts=[str(value) for value in payload.get("test_artifacts", []) if str(value)],
            suspected_implementation_artifacts=[
                str(value) for value in payload.get("suspected_implementation_artifacts", []) if str(value)
            ],
            suspected_scopes=[str(value) for value in payload.get("suspected_scopes", []) if str(value)],
            external_artifacts=[str(value) for value in payload.get("external_artifacts", []) if str(value)],
            recovery_action=str(payload.get("recovery_action", "")),
            primary_scope=str(payload.get("primary_scope", "")),
            fallback_scopes=[str(value) for value in payload.get("fallback_scopes", []) if str(value)],
            repair_instruction=str(payload.get("repair_instruction", "")),
        )

    def to_record(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "gate_id": self.gate_id,
            "scope_id": self.scope_id,
            "failure_kind": self.failure_kind,
        }
        for key, value in (
            ("failed_command", self.failed_command),
            ("failing_test", self.failing_test),
            ("traceback_excerpt", self.traceback_excerpt),
            ("expected_actual", self.expected_actual),
            ("repair_instruction", self.repair_instruction),
        ):
            if value:
                payload[key] = value
        if self.suspected_symbols:
            payload["suspected_symbols"] = list(self.suspected_symbols)
        if self.affected_artifacts:
            payload["affected_artifacts"] = list(self.affected_artifacts)
        if self.test_artifacts:
            payload["test_artifacts"] = list(self.test_artifacts)
        if self.suspected_implementation_artifacts:
            payload["suspected_implementation_artifacts"] = list(self.suspected_implementation_artifacts)
        if self.suspected_scopes:
            payload["suspected_scopes"] = list(self.suspected_scopes)
        if self.external_artifacts:
            payload["external_artifacts"] = list(self.external_artifacts)
        if self.recovery_action:
            payload["recovery_action"] = self.recovery_action
        if self.primary_scope:
            payload["primary_scope"] = self.primary_scope
        if self.fallback_scopes:
            payload["fallback_scopes"] = list(self.fallback_scopes)
        payload["fingerprint"] = self.fingerprint()
        return payload

    def is_actionable(self) -> bool:
        return bool(
            self.failure_kind
            and self.failure_kind != "unknown"
            and (
                self.failing_test
                or self.traceback_excerpt
                or self.expected_actual
                or self.affected_artifacts
            )
        )

    def fingerprint(self) -> str:
        seed = "\n".join(
            [
                self.gate_id,
                self.scope_id,
                self.failure_kind,
                self.failing_test,
                self.expected_actual,
                ",".join(self.suspected_symbols[:8]),
                ",".join(self.suspected_implementation_artifacts[:8]),
                ",".join(self.suspected_scopes[:8]),
                ",".join(self.test_artifacts[:8]),
                self.recovery_action,
                self.primary_scope,
            ]
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def summary(self) -> str:
        pieces = [f"{self.failure_kind} at {self.gate_id}"]
        if self.failing_test:
            pieces.append(f"test={self.failing_test}")
        if self.expected_actual:
            pieces.append(f"expected/actual={self.expected_actual}")
        if self.affected_artifacts:
            pieces.append("artifacts=" + ", ".join(self.affected_artifacts[:6]))
        if self.suspected_implementation_artifacts:
            pieces.append("suspects=" + ", ".join(self.suspected_implementation_artifacts[:6]))
        if self.suspected_scopes:
            pieces.append("scopes=" + ", ".join(self.suspected_scopes[:6]))
        if self.primary_scope:
            pieces.append(f"primary={self.primary_scope}")
        if self.recovery_action:
            pieces.append(f"action={self.recovery_action}")
        return "; ".join(pieces)


class DiagnosticBuilder:
    """Best-effort deterministic parser for gate failures."""

    @classmethod
    def from_gate_failure(
        cls,
        *,
        gate_id: str,
        scope_id: str,
        errors: Iterable[str],
        failed_command: str = "",
        affected_artifacts: Optional[Iterable[str]] = None,
    ) -> List[DiagnosticRecord]:
        text = "\n".join(str(error) for error in errors if str(error))
        if not text.strip():
            return []
        extracted = cls._extract_artifacts(text, scope_id=scope_id)
        provided = [str(value) for value in affected_artifacts or [] if str(value)]
        artifacts = cls._dedupe([*extracted, *provided])
        test_artifacts = cls._dedupe(path for path in artifacts if cls._is_test_artifact(path))
        suspected_impl = cls._dedupe(path for path in extracted if cls._is_implementation_artifact(path, scope_id))
        kind = cls._failure_kind(text)
        semantic_impl = cls._semantic_implementation_artifacts(scope_id, text)
        if not suspected_impl and kind:
            if kind in {"syntax_error", "import_error", "placeholder", "out_of_scope", "missing_artifact"}:
                suspected_impl = cls._dedupe(path for path in artifacts if cls._is_implementation_artifact(path, scope_id))
            elif kind in {"unittest_assertion", "unittest_failure", "quality_failure"}:
                suspected_impl = semantic_impl
        elif semantic_impl and kind in {"unittest_assertion", "unittest_failure"}:
            suspected_impl = cls._dedupe([*suspected_impl, *semantic_impl])
        external = cls._dedupe(path for path in extracted if path not in test_artifacts and path not in suspected_impl)
        suspected_scopes = cls._scopes_for_artifacts(suspected_impl, fallback_scope=scope_id)
        return [
            DiagnosticRecord(
                gate_id=gate_id,
                scope_id=scope_id,
                failure_kind=kind,
                failed_command=failed_command or cls._failed_command(text),
                failing_test=cls._failing_test(text),
                traceback_excerpt=cls._traceback_excerpt(text),
                expected_actual=cls._expected_actual(text),
                suspected_symbols=cls._suspected_symbols(text),
                affected_artifacts=artifacts,
                test_artifacts=test_artifacts,
                suspected_implementation_artifacts=suspected_impl,
                suspected_scopes=suspected_scopes,
                external_artifacts=external,
                repair_instruction=cls._repair_instruction(kind, scope_id, suspected_impl or test_artifacts or artifacts),
            )
        ]

    @classmethod
    def from_final_gate_failure(
        cls,
        *,
        errors: Iterable[str],
        required_artifacts: Iterable[str],
        artifact_scope_map: Optional[Dict[str, str]] = None,
        failed_command: str = "",
    ) -> List[DiagnosticRecord]:
        return FinalDiagnosticResolver.resolve(
            errors=errors,
            required_artifacts=required_artifacts,
            artifact_scope_map=artifact_scope_map,
            failed_command=failed_command,
        )

    @staticmethod
    def from_records(records: Iterable[Dict[str, Any]]) -> List[DiagnosticRecord]:
        diagnostics: List[DiagnosticRecord] = []
        for record in records:
            if isinstance(record, dict):
                diagnostics.append(DiagnosticRecord.from_mapping(record))
        return diagnostics

    @staticmethod
    def _failure_kind(text: str) -> str:
        lower = text.lower()
        if "syntax validation failed" in lower or "syntaxerror" in lower:
            return "syntax_error"
        if "import validation failed" in lower or "importerror" in lower or "modulenotfounderror" in lower:
            return "import_error"
        if "placeholder" in lower or "notimplemented" in lower or "not implemented" in lower:
            return "placeholder"
        if "outside work-item artifact" in lower or "unexpected writes" in lower:
            return "out_of_scope"
        if "all discovered tests were skipped" in lower or "no executable tests ran" in lower or "mock-only" in lower:
            return "invalid_tests"
        if "unit test validation failed" in lower or "unittest discovery failed" in lower:
            if any(marker in lower for marker in ("assertionerror", "fail:", "failed (failures=", "failed (errors=")):
                return "unittest_assertion"
            return "unittest_failure"
        if "interface missing" in lower or "ambiguous api" in lower:
            return "missing_or_ambiguous_interface"
        if any(
            marker in lower
            for marker in (
                "llm returned an empty",
                "tool intent",
                "json parse",
                "timed out",
                "infra_failure",
                '"failure_kind": "infra"',
                "llm backend",
            )
        ):
            return "infra"
        if any(
            marker in lower
            for marker in (
                "target artifact missing",
                "target artifacts missing",
                "target artifact(s) missing",
                "required artifact missing",
                "required artifacts missing",
                "required artifact(s) missing",
            )
        ):
            return "missing_artifact"
        return "quality_failure"

    @staticmethod
    def _failed_command(text: str) -> str:
        match = re.search(r"(?:command|cmd)\s*[:=]\s*([^\n]+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if "unittest" in text.lower():
            return "python -m unittest"
        if "compile" in text.lower():
            return "python -m compileall"
        return ""

    @staticmethod
    def _failing_test(text: str) -> str:
        patterns = [
            r"^(?:FAIL|ERROR):\s+([^\n]+)",
            r"FAILED\s+\((?:failures|errors)=\d+\).*?([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)",
            r"(test_[A-Za-z0-9_]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _traceback_excerpt(text: str) -> str:
        marker_index = text.lower().find("traceback")
        if marker_index < 0:
            marker_index = text.lower().find("assertionerror")
        if marker_index < 0:
            marker_index = 0
        excerpt = text[marker_index : marker_index + _MAX_EXCERPT].strip()
        return excerpt

    @staticmethod
    def _expected_actual(text: str) -> str:
        compact = " ".join(text.split())
        match = re.search(r"AssertionError:\s+(.{1,160}?)(?:\s*!=\s*|\s+is not\s+)(.{1,160}?)(?:\s*:|\s*$)", compact)
        if match:
            return f"{match.group(1).strip()} != {match.group(2).strip()}"
        match = re.search(r"Expected\s+(.{1,120}?)\s+(?:but got|got)\s+(.{1,120}?)(?:\.|$)", compact, flags=re.IGNORECASE)
        if match:
            return f"expected {match.group(1).strip()}, got {match.group(2).strip()}"
        return ""

    @staticmethod
    def _suspected_symbols(text: str) -> List[str]:
        symbols: List[str] = []
        for pattern in (
            r"\b([A-Za-z_][A-Za-z0-9_]{2,})\(",
            r"`([A-Za-z_][A-Za-z0-9_]{2,})`",
            r"'([A-Za-z_][A-Za-z0-9_]{2,})'",
        ):
            symbols.extend(re.findall(pattern, text))
        ignored = {"Traceback", "AssertionError", "File", "line", "self", "True", "False"}
        return DiagnosticBuilder._dedupe(symbol for symbol in symbols if symbol not in ignored)[:12]

    @staticmethod
    def _extract_artifacts(text: str, scope_id: str = "") -> List[str]:
        candidates: List[str] = []
        for match in re.finditer(r"([A-Za-z0-9_./\\-]+\.py)", text):
            path = match.group(1).replace("\\", "/")
            if DiagnosticBuilder._looks_like_external_python_path(path):
                continue
            pieces = [piece for piece in path.split("/") if piece]
            if not pieces:
                continue
            if "tests" in pieces:
                idx = pieces.index("tests")
                candidates.append("/".join(pieces[idx:]))
            elif scope_id and scope_id in pieces:
                idx = pieces.index(scope_id)
                candidates.append("/".join(pieces[idx:]))
            elif len(pieces) >= 2:
                candidates.append("/".join(pieces[-2:]))
            else:
                candidates.append(os.path.basename(path))
        return candidates

    @staticmethod
    def _looks_like_external_python_path(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/").strip("/")
        lower = normalized.lower()
        pieces = [piece.lower() for piece in normalized.split("/") if piece]
        if any(piece in {"site-packages", "dist-packages", "lib-dynload", "__pycache__"} for piece in pieces):
            return True
        if any(piece.startswith("python3.") or piece.startswith("python2.") for piece in pieces):
            return True
        if "python.framework" in lower or "/lib/python" in f"/{lower}":
            return True
        if pieces and pieces[0] in {
            "python",
            "python3",
            "json",
            "unittest",
            "importlib",
            "argparse",
            "dataclasses",
            "pathlib",
            "typing",
            "collections",
            "contextlib",
        }:
            return True
        return False

    @staticmethod
    def _normalize_artifact(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/").strip("/")
        return normalized[2:] if normalized.startswith("./") else normalized

    @classmethod
    def _canonical_artifact(cls, path: str, known_artifacts: Iterable[str]) -> str:
        normalized = cls._normalize_artifact(path)
        known = [cls._normalize_artifact(value) for value in known_artifacts if str(value).strip()]
        for artifact in known:
            if artifact == normalized or artifact.endswith("/" + normalized) or normalized.endswith("/" + artifact):
                return artifact
        return normalized

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    @classmethod
    def _is_implementation_artifact(cls, path: str, scope_id: str = "") -> bool:
        normalized = str(path or "").replace("\\", "/").strip("/")
        if not normalized.endswith(".py") or cls._is_test_artifact(normalized):
            return False
        pieces = [piece for piece in normalized.split("/") if piece]
        lowered = [piece.lower() for piece in pieces]
        if any(piece in {"site-packages", "dist-packages", "lib-dynload", "__pycache__"} for piece in lowered):
            return False
        if cls._looks_like_external_python_path(normalized):
            return False
        if pieces and pieces[0] in {"python", "python3", "json", "unittest", "importlib", "argparse", "dataclasses"}:
            return False
        if normalized.startswith(("private/tmp/", "tmp/", "var/folders/")) and scope_id and scope_id not in pieces:
            return False
        return True

    @classmethod
    def _scopes_for_artifacts(cls, artifacts: Iterable[str], fallback_scope: str = "") -> List[str]:
        scopes: List[str] = []
        for artifact in artifacts:
            normalized = cls._normalize_artifact(artifact)
            pieces = [piece.lower() for piece in normalized.split("/") if piece]
            name = pieces[-1] if pieces else ""
            if name == "__init__.py":
                scopes.append("package")
                continue
            if name in {"cli.py", "main.py", "__main__.py"}:
                scopes.append("interface")
                continue
            for scope in ("package", "domain", "core", "planning", "ai", "io", "interface", "tests"):
                if scope in pieces:
                    scopes.append(scope)
                    break
            else:
                if fallback_scope and fallback_scope != "integration":
                    scopes.append(fallback_scope)
        return cls._dedupe(scopes)

    @staticmethod
    def _semantic_implementation_artifacts(scope_id: str, text: str) -> List[str]:
        """Infer likely implementation files from public symbols in gate failures.

        This is intentionally small and deterministic. It only supplies owner
        hints when traceback evidence points at tests but the failing symbols
        clearly belong to a functional scope.
        """

        lower = str(text or "").lower()
        scope = str(scope_id or "").lower()
        out: List[str] = []

        def add(path: str) -> None:
            if path not in out:
                out.append(path)

        if scope in {"planning", "ai"}:
            prefix = "planning" if scope == "planning" else "ai"
            if any(token in lower for token in ("policy", "policies", "choose_policy", "recommend_actions", "survival", "science")):
                add(f"{prefix}/policies.py")
            if any(token in lower for token in ("heuristic", "rank_actions", "predict_delta", "delta_score", "normalize", "score")):
                add(f"{prefix}/heuristics.py")
            if any(token in lower for token in ("planner", "colonyplanner", "plan_turn", "plan_actions", "commands")):
                add(f"{prefix}/planner.py")
        elif scope == "core":
            if any(token in lower for token in ("tick", "ticks_between", "turnclock", "phase", "scheduledaction", "tickscheduler")):
                add("core/ticks.py")
            if any(token in lower for token in ("engine", "simulation", "turnreport", "run_turn", "advance")):
                add("core/engine.py")
            if any(token in lower for token in ("economy", "income", "cost", "production")):
                add("core/economy.py")
            if any(token in lower for token in ("construction", "build", "queue")):
                add("core/construction.py")
            if "research" in lower:
                add("core/research.py")
            if any(token in lower for token in ("logistics", "route", "transport")):
                add("core/logistics.py")
            if any(token in lower for token in ("diplomacy", "faction")):
                add("core/diplomacy.py")
            if any(token in lower for token in ("disaster", "event")):
                add("core/disasters.py")
            if "victory" in lower:
                add("core/victory.py")
        elif scope == "domain":
            if "population" in lower:
                add("domain/population.py")
            if any(token in lower for token in ("resource", "inventory", "stockpile")):
                add("domain/resources.py")
            if any(token in lower for token in ("colony", "state", "snapshot")):
                add("domain/colony.py")
            if any(token in lower for token in ("building", "structure")):
                add("domain/buildings.py")
            if any(token in lower for token in ("technology", "tech")):
                add("domain/technology.py")
            if "invariant" in lower:
                add("domain/invariants.py")
            if "event" in lower:
                add("domain/events.py")
        elif scope == "io":
            if any(token in lower for token in ("save", "load", "serialize", "deserialize", "roundtrip")):
                add("io/save_load.py")
            if "scenario" in lower:
                add("io/scenarios.py")
            if any(token in lower for token in ("map", "terrain", "grid")):
                add("io/maps.py")
        elif scope == "interface":
            if any(token in lower for token in ("cli", "argparse", "stdout", "stderr", "json", "command", "entrypoint")):
                add("interface/cli.py")
            if any(token in lower for token in ("repl", "render", "prompt")):
                add("interface/repl.py")
        elif scope == "package":
            if any(token in lower for token in ("__all__", "__init__", "version", "export", "package")):
                add("__init__.py")
        return out

    @classmethod
    def _semantic_scopes_for_final_failure(cls, text: str) -> List[str]:
        lower = text.lower()
        scopes: List[str] = []
        patterns = [
            ("interface", ("cli", "repl", "command", "parser", "entrypoint", "stdout", "argument", "default_scenario")),
            ("io", ("scenario", "save", "load", "serialize", "deserialize", "persistent", "resume", "roundtrip", "map")),
            ("core", ("engine", "turn", "tick", "simulation", "economy", "construction", "research", "victory")),
            ("domain", ("domain", "colony", "population", "resource", "building", "technology", "invariant")),
            ("planning", ("planning", "planner", "policy", "heuristic", "recommend")),
            ("ai", ("ai", "colonyplanner")),
            ("package", ("package_exports", "__init__", "version", "__all__")),
        ]
        for scope, tokens in patterns:
            if any(token in lower for token in tokens):
                scopes.append(scope)
        if "population" in lower and "engine" in lower:
            scopes.extend(["domain", "core"])
        if "turn" in lower and "scenario" in lower:
            scopes.extend(["core", "io"])
        if "unexpected keyword argument 'policy'" in lower:
            scopes.extend(["interface", "ai"])
        return cls._dedupe(scopes)

    @staticmethod
    def _repair_instruction(kind: str, scope_id: str, artifacts: List[str]) -> str:
        target_text = ", ".join(artifacts[:6]) if artifacts else f"{scope_id} owned artifacts"
        if kind in {"invalid_tests"}:
            return f"Regenerate executable scope tests for {scope_id}; do not use all-skip or mock-only tests."
        if kind in {"missing_or_ambiguous_interface"}:
            return f"Repair or clarify the {scope_id} interface contract before implementation work continues."
        if kind == "infra":
            return "Retry the same gate attempt; do not mutate implementation for infrastructure failure."
        return f"Repair the {scope_id} implementation using the failing assertion/traceback; focus on {target_text}."

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        seen = set()
        output: List[str] = []
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(normalized)
        return output


class FinalDiagnosticResolver:
    """Resolve final gate failures to the smallest likely functional owner."""

    IMPLEMENTATION_ACTION = "implementation_repair"
    TEST_ACTION = "test_regeneration"
    SCALE_ACTION = "scale_completion"
    REPLAN_ACTION = "interface_replan"
    INFRA_ACTION = "infra_retry"

    @classmethod
    def resolve(
        cls,
        *,
        errors: Iterable[str],
        required_artifacts: Iterable[str],
        artifact_scope_map: Optional[Dict[str, str]] = None,
        failed_command: str = "",
    ) -> List[DiagnosticRecord]:
        text = "\n".join(str(error) for error in errors if str(error))
        if not text.strip():
            return []
        required = DiagnosticBuilder._dedupe(str(value) for value in required_artifacts if str(value))
        scope_by_artifact = {
            DiagnosticBuilder._normalize_artifact(path): str(scope)
            for path, scope in (artifact_scope_map or {}).items()
            if str(path).strip() and str(scope).strip()
        }
        base = DiagnosticBuilder.from_gate_failure(
            gate_id="final",
            scope_id="integration",
            errors=[text],
            failed_command=failed_command,
            affected_artifacts=[],
        )[0]
        kind = base.failure_kind
        missing_tests = cls._missing_test_artifacts(text, required, base)
        if missing_tests:
            return [
                DiagnosticRecord(
                    gate_id="final",
                    scope_id="integration",
                    failure_kind="missing_test_artifact",
                    failed_command=base.failed_command,
                    failing_test=base.failing_test or cls._test_label(missing_tests[0]),
                    traceback_excerpt=base.traceback_excerpt,
                    expected_actual="missing test artifact(s): " + ", ".join(missing_tests),
                    suspected_symbols=base.suspected_symbols,
                    affected_artifacts=list(missing_tests),
                    test_artifacts=list(missing_tests),
                    suspected_implementation_artifacts=[],
                    suspected_scopes=["tests"],
                    external_artifacts=[],
                    recovery_action=cls.TEST_ACTION,
                    primary_scope="tests",
                    fallback_scopes=[],
                    repair_instruction=(
                        "Regenerate the missing final/test artifacts with executable unittest coverage: "
                        + ", ".join(missing_tests)
                        + ". Do not reopen implementation owners until the declared tests exist and fail against real behavior."
                    ),
                )
            ]
        semantic_scopes = cls._semantic_owner_scopes(text)
        traceback_impl = cls._traceback_implementation_artifacts_for_final(text, required)
        semantic_impl = cls._semantic_implementation_artifacts_for_final(text, required, semantic_scopes)
        explicit_impl = DiagnosticBuilder._dedupe(
            [
                *traceback_impl,
                *cls._explicit_implementation_artifacts(base, required),
                *semantic_impl,
            ]
        )
        explicit_impl = cls._prioritize_final_implementation_artifacts(
            explicit_impl,
            text=text,
            traceback_artifacts=traceback_impl,
            semantic_scopes=semantic_scopes,
            scope_by_artifact=scope_by_artifact,
        )
        explicit_scopes = cls._scopes_for_artifacts(explicit_impl, scope_by_artifact)
        ordered_scopes = cls._ordered_owner_scopes(kind, explicit_scopes, semantic_scopes, required, scope_by_artifact)
        primary_scope = ordered_scopes[0] if ordered_scopes else ""
        fallback_scopes = ordered_scopes[1:]
        action = cls._recovery_action(kind)

        suspected_impl = explicit_impl
        if (
            not suspected_impl
            and kind in {"unittest_assertion", "unittest_failure", "quality_failure", "missing_artifact"}
        ):
            scope_set = set(ordered_scopes)
            if scope_set:
                primary_scope_set = {primary_scope} if primary_scope else scope_set
                scoped_required = [
                    artifact
                    for artifact in required
                    if DiagnosticBuilder._is_implementation_artifact(artifact)
                    and scope_by_artifact.get(DiagnosticBuilder._normalize_artifact(artifact), "") in primary_scope_set
                ]
                suspected_impl = DiagnosticBuilder._dedupe([*suspected_impl, *scoped_required[:3]])

        if not primary_scope and suspected_impl:
            scopes = cls._scopes_for_artifacts(suspected_impl, scope_by_artifact)
            primary_scope = scopes[0] if scopes else ""
            fallback_scopes = scopes[1:]
            ordered_scopes = scopes

        external = [
            path
            for path in base.external_artifacts
            if path not in suspected_impl and path not in base.test_artifacts
        ]
        affected = DiagnosticBuilder._dedupe(
            [
                *base.affected_artifacts,
                *base.test_artifacts,
                *suspected_impl,
            ]
        )
        return [
            DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind=kind,
                failed_command=base.failed_command,
                failing_test=base.failing_test,
                traceback_excerpt=base.traceback_excerpt,
                expected_actual=base.expected_actual,
                suspected_symbols=base.suspected_symbols,
                affected_artifacts=affected,
                test_artifacts=base.test_artifacts,
                suspected_implementation_artifacts=suspected_impl,
                suspected_scopes=ordered_scopes,
                external_artifacts=external,
                recovery_action=action,
                primary_scope=primary_scope,
                fallback_scopes=fallback_scopes,
                repair_instruction=cls._repair_instruction(
                    kind=kind,
                    action=action,
                    primary_scope=primary_scope,
                    fallback_scopes=fallback_scopes,
                    artifacts=suspected_impl or affected,
                ),
            )
        ]

    @staticmethod
    def _explicit_implementation_artifacts(base: DiagnosticRecord, required: List[str]) -> List[str]:
        canonical = DiagnosticBuilder._dedupe(
            DiagnosticBuilder._canonical_artifact(path, required)
            for path in base.suspected_implementation_artifacts
            if path
        )
        return [path for path in canonical if path and DiagnosticBuilder._is_implementation_artifact(path)]

    @staticmethod
    def _traceback_implementation_artifacts_for_final(text: str, required: List[str]) -> List[str]:
        artifacts: List[str] = []
        required_set = {DiagnosticBuilder._normalize_artifact(path) for path in required}
        for match in re.finditer(r"File\s+['\"]([^'\"]+\.py)['\"]", str(text or "")):
            artifact = DiagnosticBuilder._canonical_artifact(match.group(1), required)
            normalized = DiagnosticBuilder._normalize_artifact(artifact)
            if not DiagnosticBuilder._is_implementation_artifact(normalized):
                continue
            if required_set and normalized not in required_set:
                continue
            artifacts.append(normalized)
        return DiagnosticBuilder._dedupe(artifacts)

    @classmethod
    def _prioritize_final_implementation_artifacts(
        cls,
        artifacts: List[str],
        *,
        text: str,
        traceback_artifacts: List[str],
        semantic_scopes: List[str],
        scope_by_artifact: Dict[str, str],
    ) -> List[str]:
        normalized = DiagnosticBuilder._dedupe(
            DiagnosticBuilder._normalize_artifact(path)
            for path in artifacts
            if path and DiagnosticBuilder._is_implementation_artifact(path)
        )
        if not normalized:
            return []

        package_failure = cls._is_package_owner_failure(text)
        if not package_failure:
            non_package = [
                artifact
                for artifact in normalized
                if cls._scope_for_artifact(artifact, scope_by_artifact) != "package"
            ]
            if non_package:
                normalized = non_package

        traceback_scopes = DiagnosticBuilder._dedupe(
            cls._scope_for_artifact(artifact, scope_by_artifact)
            for artifact in traceback_artifacts
            if artifact
            and (package_failure or cls._scope_for_artifact(artifact, scope_by_artifact) != "package")
        )
        scope_order = [
            *[scope for scope in traceback_scopes if scope and scope != "integration"],
            *[
                scope
                for scope in semantic_scopes
                if scope and scope != "integration" and scope not in traceback_scopes
            ],
        ]

        def rank(item: tuple[int, str]) -> tuple[int, int, int]:
            index, artifact = item
            scope = cls._scope_for_artifact(artifact, scope_by_artifact)
            package_noise = 0 if package_failure or scope != "package" else 1
            scope_index = scope_order.index(scope) if scope in scope_order else 99
            return (package_noise, scope_index, index)

        return [artifact for _index, artifact in sorted(enumerate(normalized), key=rank)]

    @staticmethod
    def _is_package_owner_failure(text: str) -> bool:
        lower = str(text or "").lower()
        return any(
            marker in lower
            for marker in (
                "package_exports",
                "package export",
                "package import",
                "package __all__",
                "__all__",
                "from package",
                "not found in package",
            )
        )

    @staticmethod
    def _scope_for_artifact(artifact: str, scope_by_artifact: Dict[str, str]) -> str:
        normalized = DiagnosticBuilder._normalize_artifact(artifact)
        scope = scope_by_artifact.get(normalized, "")
        if scope:
            return scope
        pieces = [piece.lower() for piece in normalized.split("/") if piece]
        name = pieces[-1] if pieces else ""
        if name == "__init__.py":
            return "package"
        if name in {"cli.py", "main.py", "__main__.py"}:
            return "interface"
        for known in ("package", "domain", "core", "planning", "ai", "io", "interface", "tests"):
            if known in pieces:
                return known
        return ""

    @classmethod
    def _missing_test_artifacts(
        cls,
        text: str,
        required: List[str],
        base: DiagnosticRecord,
    ) -> List[str]:
        lower = str(text or "").lower()
        missing_markers = (
            "target artifact missing",
            "target artifacts missing",
            "target artifact(s) missing",
            "required artifact missing",
            "required artifacts missing",
            "required artifact(s) missing",
            "no test artifact exists",
            "tests are declared",
        )
        if not any(marker in lower for marker in missing_markers):
            return []
        extracted_tests = [
            DiagnosticBuilder._canonical_artifact(path, required)
            for path in [*base.test_artifacts, *base.affected_artifacts]
            if DiagnosticBuilder._is_test_artifact(path)
        ]
        if not extracted_tests and "no test artifact exists" in lower:
            extracted_tests = [
                artifact
                for artifact in required
                if DiagnosticBuilder._is_test_artifact(artifact)
            ]
        required_set = {DiagnosticBuilder._normalize_artifact(path) for path in required}
        return DiagnosticBuilder._dedupe(
            path
            for path in extracted_tests
            if DiagnosticBuilder._is_test_artifact(path)
            and (not required_set or DiagnosticBuilder._normalize_artifact(path) in required_set)
        )

    @staticmethod
    def _test_label(path: str) -> str:
        name = os.path.basename(str(path or "")).rsplit(".", 1)[0]
        return name or "missing_tests"

    @staticmethod
    def _scopes_for_artifacts(artifacts: Iterable[str], scope_by_artifact: Dict[str, str]) -> List[str]:
        scopes: List[str] = []
        for artifact in artifacts:
            normalized = DiagnosticBuilder._normalize_artifact(artifact)
            scope = scope_by_artifact.get(normalized, "")
            if not scope:
                for known_artifact, known_scope in scope_by_artifact.items():
                    known = DiagnosticBuilder._normalize_artifact(known_artifact)
                    if known.endswith("/" + normalized) or normalized.endswith("/" + known):
                        scope = known_scope
                        break
            if scope and scope != "integration":
                scopes.append(scope)
        return DiagnosticBuilder._dedupe(scopes)

    @staticmethod
    def _semantic_implementation_artifacts_for_final(
        text: str,
        required: List[str],
        semantic_scopes: List[str],
    ) -> List[str]:
        candidates: List[str] = []
        for scope in semantic_scopes:
            candidates.extend(DiagnosticBuilder._semantic_implementation_artifacts(scope, text))
        canonical = [
            DiagnosticBuilder._canonical_artifact(path, required)
            for path in candidates
            if path
        ]
        required_set = {DiagnosticBuilder._normalize_artifact(path) for path in required}
        return [
            path
            for path in DiagnosticBuilder._dedupe(canonical)
            if path and DiagnosticBuilder._is_implementation_artifact(path)
            and (not required_set or DiagnosticBuilder._normalize_artifact(path) in required_set)
        ]

    @classmethod
    def _ordered_owner_scopes(
        cls,
        kind: str,
        explicit_scopes: List[str],
        semantic_scopes: List[str],
        required: List[str],
        scope_by_artifact: Dict[str, str],
    ) -> List[str]:
        if explicit_scopes:
            return DiagnosticBuilder._dedupe([*explicit_scopes, *semantic_scopes])
        return DiagnosticBuilder._dedupe(semantic_scopes)

    @staticmethod
    def _semantic_owner_scopes(text: str) -> List[str]:
        lower = text.lower()
        scopes: List[str] = []

        def add(scope: str) -> None:
            if scope not in scopes:
                scopes.append(scope)

        cli_markers = (
            "cli",
            "repl",
            "stdout",
            "stderr",
            "jsondecodeerror",
            "argumentparser",
            "argparse",
            "entrypoint",
            "validate --json",
            "command",
        )
        if any(marker in lower for marker in cli_markers):
            add("interface")
        if any(
            marker in lower
            for marker in ("package_exports", "__all__", "package export", "package import", "from package")
        ):
            add("package")
        if any(marker in lower for marker in ("save", "load", "scenario", "serialize", "deserialize", "roundtrip", "map")):
            add("io")
        if any(marker in lower for marker in ("engine", "turn", "tick", "simulation", "economy", "construction", "research", "victory")):
            add("core")
        if any(marker in lower for marker in ("domain", "colony", "population", "resource", "building", "technology", "invariant")):
            add("domain")
        if any(marker in lower for marker in ("planning", "planner", "policy", "heuristic", "recommend")):
            add("planning")
        if "colonyplanner" in lower or "ai/" in lower:
            add("ai")

        if "population" in lower and "engine" in lower:
            add("domain")
            add("core")
        if "turn" in lower and "scenario" in lower:
            add("core")
            add("io")
        if "unexpected keyword argument 'policy'" in lower:
            add("interface")
            add("planning")
        return scopes

    @classmethod
    def _recovery_action(cls, kind: str) -> str:
        if kind == "infra":
            return cls.INFRA_ACTION
        if kind == "invalid_tests":
            return cls.TEST_ACTION
        if kind == "missing_or_ambiguous_interface":
            return cls.REPLAN_ACTION
        return cls.IMPLEMENTATION_ACTION

    @staticmethod
    def _repair_instruction(
        *,
        kind: str,
        action: str,
        primary_scope: str,
        fallback_scopes: List[str],
        artifacts: List[str],
    ) -> str:
        targets = ", ".join(artifacts[:6]) if artifacts else (primary_scope or "the owning implementation")
        if action == FinalDiagnosticResolver.SCALE_ACTION:
            return (
                "Complete meaningful project scale in the owning implementation batches. Add real behavior, "
                "edge cases, public API coverage, and tests; do not pad or add dead filler."
            )
        if action == FinalDiagnosticResolver.TEST_ACTION:
            if kind == "missing_test_artifact":
                return f"Regenerate missing final/test artifacts: {targets}. Keep tests executable, real-importing, and non-mock-only."
            return "Repair invalid final integration tests; keep tests executable, real-importing, and non-mock-only."
        if action == FinalDiagnosticResolver.REPLAN_ACTION:
            return "Repair or clarify the missing final integration interface before retrying implementation."
        owner = primary_scope or "the owning team"
        fallback = f" Fallback owners after repeated failure: {', '.join(fallback_scopes)}." if fallback_scopes else ""
        return f"Repair final integration failure in {owner}; focus on {targets}.{fallback}"
