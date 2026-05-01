"""Quality, gate, and evaluation helpers."""

from ContractCoding.quality.failure_router import FailureRoute, FailureRouter
from ContractCoding.quality.diagnostics import DiagnosticBuilder, DiagnosticRecord, FinalDiagnosticResolver
from ContractCoding.quality.gates import GateChecker
from ContractCoding.quality.owner import OwnerResolution, OwnerResolver
from ContractCoding.quality.review import GateReviewParser, GateReviewVerdict
from ContractCoding.quality.self_check import SelfChecker

__all__ = [
    "EvalCase",
    "EvalResult",
    "EvalSuiteRunner",
    "EvalSummary",
    "FailureRoute",
    "FailureRouter",
    "DiagnosticBuilder",
    "DiagnosticRecord",
    "FinalDiagnosticResolver",
    "GateChecker",
    "OwnerResolution",
    "OwnerResolver",
    "GateReviewParser",
    "GateReviewVerdict",
    "SelfChecker",
    "default_real_task_eval_cases",
]


def __getattr__(name):
    if name in {"EvalCase", "EvalResult", "EvalSuiteRunner", "EvalSummary", "default_real_task_eval_cases"}:
        from ContractCoding.quality import evals

        return getattr(evals, name)
    raise AttributeError(name)
