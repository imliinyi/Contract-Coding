__all__ = [
    "AgentExecutor",
    "AgentRunner",
    "Engine",
    "GraphTraverser",
    "Orchestrator",
]


def __getattr__(name: str):
    if name == "Engine":
        from ContractCoding.orchestration.engine import Engine

        return Engine
    if name in {"AgentRunner", "AgentExecutor"}:
        from ContractCoding.orchestration.runner import AgentExecutor, AgentRunner

        return {"AgentRunner": AgentRunner, "AgentExecutor": AgentExecutor}[name]
    if name in {"GraphTraverser", "Orchestrator"}:
        from ContractCoding.orchestration.traverser import GraphTraverser, Orchestrator

        return {"GraphTraverser": GraphTraverser, "Orchestrator": Orchestrator}[name]
    raise AttributeError(name)
