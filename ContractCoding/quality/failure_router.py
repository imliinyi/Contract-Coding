"""Failure classification for gate-centric recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ContractCoding.quality.diagnostics import DiagnosticRecord


RECOVER_INFRA = "infra_retry"
RECOVER_IMPLEMENTATION = "implementation_repair"
RECOVER_TEST = "test_regeneration"
RECOVER_REPLAN = "diagnostic_replan"
RECOVER_SCALE = "scale_completion"
RECOVER_HUMAN = "needs_human"

_STRUCTURED_ACTIONS = {
    "infra_retry": RECOVER_INFRA,
    "implementation_repair": RECOVER_IMPLEMENTATION,
    "test_repair": RECOVER_TEST,
    "test_regeneration": RECOVER_TEST,
    "diagnostic_replan": RECOVER_REPLAN,
    "interface_replan": RECOVER_REPLAN,
    "scale_completion": RECOVER_SCALE,
    "needs_human": RECOVER_HUMAN,
}


@dataclass(frozen=True)
class FailureRoute:
    action: str
    reason: str


class FailureRouter:
    def classify_diagnostics(self, diagnostics: Iterable[DiagnosticRecord]) -> FailureRoute:
        records = list(diagnostics)
        if not records:
            return FailureRoute(RECOVER_HUMAN, "diagnostic evidence is required before repair")
        actions = [self._classify_diagnostic(record) for record in records]
        implementation_route = next((route for route in actions if route.action == RECOVER_IMPLEMENTATION), None)
        test_route = next((route for route in actions if route.action == RECOVER_TEST), None)
        if implementation_route and test_route and any(self._is_implementation_blocker(record) for record in records):
            return FailureRoute(
                RECOVER_IMPLEMENTATION,
                "implementation blocker is present alongside missing/invalid tests; repair executable public behavior first",
            )
        for action in (
            RECOVER_REPLAN,
            RECOVER_TEST,
            RECOVER_IMPLEMENTATION,
            RECOVER_SCALE,
            RECOVER_INFRA,
            RECOVER_HUMAN,
        ):
            match = next((route for route in actions if route.action == action), None)
            if match:
                return match
        return actions[0]

    def _classify_diagnostic(self, diagnostic: DiagnosticRecord) -> FailureRoute:
        structured = _STRUCTURED_ACTIONS.get(str(diagnostic.recovery_action or "").strip())
        if structured:
            return FailureRoute(structured, diagnostic.repair_instruction or f"structured final diagnostic requested {structured}")
        kind = diagnostic.failure_kind
        if kind == "infra":
            return FailureRoute(RECOVER_INFRA, "infrastructure/tool failure")
        if kind == "missing_test_artifact":
            return FailureRoute(RECOVER_TEST, "declared final/test artifact is missing")
        if kind in {"invalid_tests"}:
            return FailureRoute(RECOVER_TEST, "generated tests are invalid")
        if kind == "missing_artifact" and any(self._is_test_artifact(path) for path in diagnostic.affected_artifacts):
            return FailureRoute(RECOVER_TEST, "declared test artifact is missing")
        if kind in {"missing_or_ambiguous_interface"}:
            return FailureRoute(RECOVER_REPLAN, "contract or interface needs diagnostic replan")
        if kind in {"unittest_assertion", "unittest_failure"}:
            if diagnostic.gate_id == "final" or diagnostic.scope_id == "integration":
                if diagnostic.suspected_implementation_artifacts or diagnostic.suspected_scopes:
                    return FailureRoute(
                        RECOVER_IMPLEMENTATION,
                        "final integration tests failed against suspected implementation scopes",
                    )
                return FailureRoute(
                    RECOVER_IMPLEMENTATION,
                    "final integration failure requires implementation triage",
                )
            if diagnostic.suspected_implementation_artifacts:
                return FailureRoute(RECOVER_IMPLEMENTATION, "tests failed against suspected implementation artifacts")
            if diagnostic.test_artifacts:
                return FailureRoute(
                    RECOVER_IMPLEMENTATION,
                    "team gate tests failed; repair the owning implementation scope unless tests are structurally invalid",
                )
            return FailureRoute(RECOVER_IMPLEMENTATION, "test failure requires implementation triage")
        if kind in {
            "syntax_error",
            "import_error",
            "placeholder",
            "out_of_scope",
            "missing_artifact",
            "quality_failure",
        }:
            return FailureRoute(RECOVER_IMPLEMENTATION, "deterministic quality gate failed")
        return FailureRoute(RECOVER_IMPLEMENTATION, "quality failure")

    @staticmethod
    def _is_implementation_blocker(diagnostic: DiagnosticRecord) -> bool:
        if diagnostic.failure_kind == "missing_artifact" and diagnostic.affected_artifacts:
            if all(FailureRouter._is_test_artifact(path) for path in diagnostic.affected_artifacts):
                return False
        return diagnostic.failure_kind in {
            "syntax_error",
            "import_error",
            "placeholder",
            "unittest_assertion",
            "unittest_failure",
            "missing_artifact",
            "quality_failure",
        } and bool(
            diagnostic.suspected_implementation_artifacts
            or diagnostic.suspected_scopes
            or diagnostic.affected_artifacts
            or diagnostic.traceback_excerpt
            or diagnostic.expected_actual
        )

    @staticmethod
    def _is_test_artifact(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return normalized.endswith(".py") and (
            name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{normalized}"
        )

    def classify(self, text: str) -> FailureRoute:
        lower = str(text or "").lower()
        if any(marker in lower for marker in ("unit test validation failed", "unittest discovery failed", "test failure", "assertionerror", "fail: test_")):
            return FailureRoute(RECOVER_IMPLEMENTATION, "tests failed against implementation")
        if any(marker in lower for marker in ("placeholder", "syntax validation failed", "import validation failed", "target artifact missing", "outside work-item artifact")):
            return FailureRoute(RECOVER_IMPLEMENTATION, "implementation artifact failed self-check")
        if any(marker in lower for marker in ("all discovered tests were skipped", "no executable tests ran", "mock-only", "invalid_tests")):
            return FailureRoute(RECOVER_TEST, "generated tests are invalid")
        if any(
            marker in lower
            for marker in (
                "llm returned an empty",
                "llm infrastructure failure",
                "failed to create session",
                "attempt to write a readonly database",
                "operation not permitted",
                "timed out",
                "tool intent",
                "json parse",
            )
        ):
            return FailureRoute(RECOVER_INFRA, "infrastructure/tool failure")
        if any(marker in lower for marker in ("interface missing", "unknown scope", "dependency cycle", "ambiguous api")):
            return FailureRoute(RECOVER_REPLAN, "contract or interface needs diagnostic replan")
        if any(marker in lower for marker in ("requires approval", "permission denied", "source access unavailable")):
            return FailureRoute(RECOVER_HUMAN, "human approval or source access required")
        return FailureRoute(RECOVER_IMPLEMENTATION, "quality failure")
