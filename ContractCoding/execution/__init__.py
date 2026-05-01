"""Execution boundaries for ContractCoding runtime."""

__all__ = [
    "AgentExecutor",
    "AgentRunner",
    "ExecutionPlane",
    "ExecutionPlaneManager",
    "ExecutionPlanePromotionError",
    "TaskHarness",
    "TaskResult",
    "TaskSpec",
    "get_current_workspace",
    "workspace_scope",
]


def __getattr__(name):
    if name in {"TaskHarness", "TaskResult", "TaskSpec"}:
        from ContractCoding.execution.harness import TaskHarness, TaskResult, TaskSpec

        return {"TaskHarness": TaskHarness, "TaskResult": TaskResult, "TaskSpec": TaskSpec}[name]
    if name in {"ExecutionPlane", "ExecutionPlaneManager", "ExecutionPlanePromotionError"}:
        from ContractCoding.execution.planes import ExecutionPlane, ExecutionPlaneManager, ExecutionPlanePromotionError

        return {
            "ExecutionPlane": ExecutionPlane,
            "ExecutionPlaneManager": ExecutionPlaneManager,
            "ExecutionPlanePromotionError": ExecutionPlanePromotionError,
        }[name]
    if name in {"AgentRunner", "AgentExecutor"}:
        from ContractCoding.execution.runner import AgentRunner

        return AgentRunner
    if name in {"get_current_workspace", "workspace_scope"}:
        from ContractCoding.execution.workspace import get_current_workspace, workspace_scope

        return {"get_current_workspace": get_current_workspace, "workspace_scope": workspace_scope}[name]
    raise AttributeError(name)
