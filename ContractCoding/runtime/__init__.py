"""Long-running runtime components."""

__all__ = [
    "EventRecord",
    "EvidenceCollector",
    "EvidenceRecord",
    "HookManager",
    "RecoveryCoordinator",
    "RecoveryController",
    "RecoveryDecision",
    "RepairPlan",
    "RunController",
    "RunEngine",
    "RunLoop",
    "RunRecord",
    "RunStore",
    "RuntimeSettings",
    "RepairTicketRecord",
    "StepRecord",
    "TaskIndex",
    "TaskRecord",
    "TeamPlanner",
    "TeamExecutor",
    "TeamRuntime",
    "TeamResult",
    "TeamRunRecord",
    "TeamSpec",
    "ReviewBundle",
]


def __getattr__(name):
    if name == "RunEngine":
        from ContractCoding.runtime.engine import RunEngine

        return RunEngine
    if name == "RunController":
        from ContractCoding.runtime.controller import RunController

        return RunController
    if name == "RunLoop":
        from ContractCoding.runtime.run_loop import RunLoop

        return RunLoop
    if name == "RecoveryController":
        from ContractCoding.runtime.recovery import RecoveryController

        return RecoveryController
    if name in {"RecoveryCoordinator", "RecoveryDecision", "RepairPlan", "ReviewBundle"}:
        from ContractCoding.runtime.recovery import (
            RecoveryCoordinator,
            RecoveryDecision,
            RepairPlan,
            ReviewBundle,
        )

        return {
            "RecoveryCoordinator": RecoveryCoordinator,
            "RecoveryDecision": RecoveryDecision,
            "RepairPlan": RepairPlan,
            "ReviewBundle": ReviewBundle,
        }[name]
    if name in {"TeamExecutor", "TeamResult"}:
        from ContractCoding.runtime.team_executor import TeamExecutor, TeamResult

        return {"TeamExecutor": TeamExecutor, "TeamResult": TeamResult}[name]
    if name in {"TeamPlanner", "TeamRuntime", "TeamSpec"}:
        from ContractCoding.runtime.teams import TeamPlanner, TeamRuntime, TeamSpec

        return {"TeamPlanner": TeamPlanner, "TeamRuntime": TeamRuntime, "TeamSpec": TeamSpec}[name]
    if name in {"EventRecord", "RepairTicketRecord", "RunRecord", "RunStore", "StepRecord", "TeamRunRecord"}:
        from ContractCoding.runtime.store import (
            EventRecord,
            RepairTicketRecord,
            RunRecord,
            RunStore,
            StepRecord,
            TeamRunRecord,
        )

        return {
            "EventRecord": EventRecord,
            "RepairTicketRecord": RepairTicketRecord,
            "RunRecord": RunRecord,
            "RunStore": RunStore,
            "StepRecord": StepRecord,
            "TeamRunRecord": TeamRunRecord,
        }[name]
    if name == "TaskRecord":
        from ContractCoding.runtime.store import TaskRecord

        return TaskRecord
    if name == "TaskIndex":
        from ContractCoding.runtime.tasks import TaskIndex

        return TaskIndex
    if name == "HookManager":
        from ContractCoding.runtime.hooks import HookManager

        return HookManager
    if name == "RuntimeSettings":
        from ContractCoding.runtime.settings import RuntimeSettings

        return RuntimeSettings
    if name in {"EvidenceCollector", "EvidenceRecord"}:
        from ContractCoding.runtime.evidence import EvidenceCollector, EvidenceRecord

        return {"EvidenceCollector": EvidenceCollector, "EvidenceRecord": EvidenceRecord}[name]
    raise AttributeError(name)
