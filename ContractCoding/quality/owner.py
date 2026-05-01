"""Deterministic owner resolution for repair diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Dict, Iterable, List, Optional

from ContractCoding.quality.diagnostics import DiagnosticBuilder, DiagnosticRecord


@dataclass(frozen=True)
class OwnerResolution:
    primary_artifact: str = ""
    fallback_artifacts: List[str] = field(default_factory=list)
    owner_scope: str = ""
    confidence: float = 0.0
    repair_mode: str = "line_patch"
    reason: str = ""


class OwnerResolver:
    """Resolve a failing diagnostic to the narrowest likely implementation owner."""

    SYMBOL_HINTS = (
        (
            ("tick", "ticks_between", "turnclock", "phase", "scheduledaction", "tickscheduler"),
            "core/ticks.py",
            "core",
        ),
        (
            ("simulationengine", "turnreport", "run_turn", "engine", "enginesnapshot"),
            "core/engine.py",
            "core",
        ),
        (
            ("resourcebundle", "resourcespec", "resource", "inventory", "stockpile"),
            "domain/resources.py",
            "domain",
        ),
        (
            ("colonystate", "colony", "founded.resources", "snapshot"),
            "domain/colony.py",
            "domain",
        ),
        (("population", "settler", "worker"), "domain/population.py", "domain"),
        (("building", "structure"), "domain/buildings.py", "domain"),
        (("technology", "tech"), "domain/technology.py", "domain"),
        (("invariant",), "domain/invariants.py", "domain"),
        (("colonyplanner",), "ai/planner.py", "ai"),
        (("planner", "plan_turn", "plan_actions"), "planning/planner.py", "planning"),
        (("policy", "policies", "choose_policy"), "planning/policies.py", "planning"),
        (("heuristic", "rank_actions", "predict_delta", "score"), "planning/heuristics.py", "planning"),
        (("save", "load", "serialize", "deserialize", "roundtrip"), "io/save_load.py", "io"),
        (("scenario",), "io/scenarios.py", "io"),
        (("map", "terrain", "grid"), "io/maps.py", "io"),
        (("cli", "argparse", "stdout", "stderr", "jsondecodeerror", "entrypoint"), "interface/cli.py", "interface"),
        (("repl", "render", "prompt"), "interface/repl.py", "interface"),
        (("__all__", "__init__", "version", "package"), "__init__.py", "package"),
    )

    def resolve(
        self,
        diagnostic: DiagnosticRecord,
        *,
        candidate_artifacts: Optional[Iterable[str]] = None,
        owner_hints: Optional[Dict[str, str]] = None,
    ) -> OwnerResolution:
        candidates = [self._normalize(path) for path in candidate_artifacts or [] if str(path).strip()]
        hints = {self._normalize(path): str(scope) for path, scope in dict(owner_hints or {}).items()}
        evidence = self._evidence_text(diagnostic)
        semantic = self._semantic_candidates(evidence)
        if candidates:
            semantic = [path for path in semantic if self._candidate_matches(path, candidates)]
        explicit = [self._normalize(path) for path in diagnostic.suspected_implementation_artifacts if path]
        low_level = [
            self._normalize(path)
            for path in diagnostic.affected_artifacts
            if diagnostic.failure_kind in {"syntax_error", "import_error", "placeholder", "out_of_scope", "missing_artifact"}
            and not DiagnosticBuilder._is_test_artifact(path)
        ]
        ordered = DiagnosticBuilder._dedupe([*semantic, *explicit, *low_level])
        canonical = [
            self._canonical(path, candidates)
            for path in ordered
            if path
        ]
        canonical = [
            path
            for path in DiagnosticBuilder._dedupe(canonical)
            if path and DiagnosticBuilder._is_implementation_artifact(path)
        ]
        if not canonical:
            scope_candidates = [
                path
                for path in candidates
                if self._scope_for(path, hints) in {diagnostic.primary_scope, diagnostic.scope_id, *diagnostic.suspected_scopes}
                and DiagnosticBuilder._is_implementation_artifact(path)
            ]
            canonical = DiagnosticBuilder._dedupe(scope_candidates)

        primary = canonical[0] if canonical else ""
        fallback = canonical[1:]
        scope = self._scope_for(primary, hints) if primary else self._first_scope(diagnostic)
        confidence = 0.85 if semantic and primary else 0.7 if primary else 0.0
        mode = "rewrite_enclosing_function_or_class" if diagnostic.failure_kind == "syntax_error" else "line_patch"
        reason = "semantic symbol match" if semantic and primary else "diagnostic artifact match" if primary else "no owner match"
        return OwnerResolution(
            primary_artifact=primary,
            fallback_artifacts=fallback,
            owner_scope=scope,
            confidence=confidence,
            repair_mode=mode,
            reason=reason,
        )

    def _semantic_candidates(self, evidence: str) -> List[str]:
        lower = evidence.lower()
        out: List[str] = []
        for tokens, artifact, _scope in self.SYMBOL_HINTS:
            if any(token.lower() in lower for token in tokens):
                out.append(artifact)
        return DiagnosticBuilder._dedupe(out)

    @staticmethod
    def _evidence_text(diagnostic: DiagnosticRecord) -> str:
        return "\n".join(
            [
                diagnostic.failing_test,
                diagnostic.traceback_excerpt,
                diagnostic.expected_actual,
                " ".join(diagnostic.suspected_symbols),
                " ".join(diagnostic.affected_artifacts),
                " ".join(diagnostic.suspected_implementation_artifacts),
                diagnostic.repair_instruction,
            ]
        )

    @staticmethod
    def _canonical(path: str, candidates: List[str]) -> str:
        normalized = OwnerResolver._normalize(path)
        for candidate in candidates:
            if candidate == normalized or candidate.endswith("/" + normalized) or normalized.endswith("/" + candidate):
                return candidate
        return normalized

    @staticmethod
    def _candidate_matches(path: str, candidates: List[str]) -> bool:
        normalized = OwnerResolver._normalize(path)
        return any(
            candidate == normalized or candidate.endswith("/" + normalized) or normalized.endswith("/" + candidate)
            for candidate in candidates
        )

    @staticmethod
    def _scope_for(path: str, owner_hints: Dict[str, str]) -> str:
        normalized = OwnerResolver._normalize(path)
        if normalized in owner_hints:
            return owner_hints[normalized]
        pieces = [piece.lower() for piece in normalized.split("/") if piece]
        for scope in ("package", "domain", "core", "planning", "ai", "io", "interface", "tests"):
            if scope in pieces:
                return scope
        if normalized.endswith("__init__.py"):
            return "package"
        return ""

    @staticmethod
    def _first_scope(diagnostic: DiagnosticRecord) -> str:
        for scope in [diagnostic.primary_scope, *diagnostic.suspected_scopes, diagnostic.scope_id]:
            if scope and scope != "integration":
                return scope
        return ""

    @staticmethod
    def _normalize(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/").strip("/")
        return normalized[2:] if normalized.startswith("./") else normalized
