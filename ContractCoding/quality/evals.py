"""Eval harness for measuring ContractCoding task runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Dict, Iterable, List

from ContractCoding.runtime.engine import RunEngine


@dataclass
class EvalCase:
    id: str
    task: str
    tags: List[str] = field(default_factory=list)
    max_steps: int | None = None


@dataclass
class EvalResult:
    case_id: str
    run_id: str
    status: str
    health: str
    replan_recommended: bool
    metrics: Dict[str, int]
    tags: List[str] = field(default_factory=list)
    artifact_count: int = 0
    team_count: int = 0
    step_count: int = 0
    repair_ticket_count: int = 0
    replan_count: int = 0
    gate_failure_count: int = 0
    false_done_risk: bool = False
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_tool_results: int = 0

    def to_record(self) -> Dict[str, object]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "status": self.status,
            "health": self.health,
            "replan_recommended": self.replan_recommended,
            "metrics": dict(self.metrics),
            "tags": list(self.tags),
            "artifact_count": self.artifact_count,
            "team_count": self.team_count,
            "step_count": self.step_count,
            "repair_ticket_count": self.repair_ticket_count,
            "replan_count": self.replan_count,
            "gate_failure_count": self.gate_failure_count,
            "false_done_risk": self.false_done_risk,
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_tool_results": self.llm_tool_results,
        }


class EvalSuiteRunner:
    """Runs a finite eval suite through the normal RunEngine path.

    The harness is intentionally thin: eval skill/profile can inspect the
    resulting run, while this class records comparable metrics.
    """

    def __init__(self, engine: RunEngine):
        self.engine = engine

    def run_cases(self, cases: Iterable[EvalCase], suite_id: str = "adhoc", write_artifact: bool = False) -> List[EvalResult]:
        results: List[EvalResult] = []
        for case in cases:
            run_id = self.engine.start(case.task, run_immediately=True, max_steps=case.max_steps)
            status = self.engine.status(run_id)
            items = status["work_items"]
            health = status["health"]
            metrics: Dict[str, int] = {}
            for item in items:
                metrics[item.status] = metrics.get(item.status, 0) + 1
            artifact_count = len({artifact for item in items for artifact in item.target_artifacts})
            team_count = len(status.get("scope_teams", []))
            step_count = len(status.get("steps", []))
            gates = status.get("gates", [])
            repair_tickets = status.get("repair_tickets", [])
            llm_metrics = self._llm_metrics(status.get("steps", []))
            results.append(
                EvalResult(
                    case_id=case.id,
                    run_id=run_id,
                    status=status["run"].status,
                    health=health.status,
                    replan_recommended=health.replan_recommended,
                    metrics=metrics,
                    tags=list(case.tags),
                    artifact_count=artifact_count,
                    team_count=team_count,
                    step_count=step_count,
                    repair_ticket_count=len(repair_tickets),
                    replan_count=sum(1 for event in status.get("events", []) if event.event_type == "run_replanned"),
                    gate_failure_count=sum(1 for gate in gates if gate.status in {"FAILED", "BLOCKED"}),
                    false_done_risk=self._false_done_risk(status),
                    llm_prompt_tokens=llm_metrics["prompt_tokens"],
                    llm_completion_tokens=llm_metrics["completion_tokens"],
                    llm_tool_results=llm_metrics["tool_results"],
                )
            )
        if write_artifact:
            self.write_results(suite_id, results)
        return results

    def write_results(self, suite_id: str, results: Iterable[EvalResult]) -> str:
        workspace = os.path.abspath(self.engine.config.WORKSPACE_DIR)
        rel_path = os.path.join(".contractcoding", "evals", f"{suite_id}.json")
        path = os.path.join(workspace, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        result_list = list(results)
        payload = {
            "suite_id": suite_id,
            "summary": EvalSummary(result_list).to_metrics(),
            "results": [result.to_record() for result in result_list],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return rel_path.replace("\\", "/")

    @staticmethod
    def _llm_metrics(steps) -> Dict[str, int]:
        metrics = {"prompt_tokens": 0, "completion_tokens": 0, "tool_results": 0}
        for step in steps:
            output = step.output if isinstance(step.output, dict) else {}
            observed = dict(output.get("llm_observability", {}) or {})
            metrics["prompt_tokens"] += int(observed.get("prompt_tokens", 0) or 0)
            metrics["completion_tokens"] += int(observed.get("completion_tokens", 0) or 0)
            metrics["tool_results"] += int(observed.get("tool_result_count", 0) or 0)
        return metrics

    @staticmethod
    def _false_done_risk(status: Dict[str, object]) -> bool:
        run = status["run"]
        if run.status != "COMPLETED":
            return False
        gates = status.get("gates", [])
        items = status.get("work_items", [])
        return any(gate.status != "PASSED" for gate in gates) or any(item.status != "VERIFIED" for item in items)


def default_real_task_eval_cases(suite: str = "smoke") -> List[EvalCase]:
    """Representative task suites for tuning the Runtime V4 control plane.

    These are intentionally small enough for CI-style runs when max_steps is
    capped, but broad enough to expose planner/team/gate regressions across
    size and delivery type.
    """

    smoke = [
        EvalCase(
            id="coding-small-cli",
            task="Create a simple Python CLI number guessing game with game_engine.py main.py test_game_engine.py",
            tags=["coding", "small", "game"],
            max_steps=6,
        ),
    ]
    medium = [
        EvalCase(
            id="coding-medium-game",
            task="Create a medium Python terminal Tic Tac Toe game with AI, CLI, and unittest coverage",
            tags=["coding", "medium", "game"],
            max_steps=10,
        ),
        EvalCase(
            id="research-synthesis",
            task="Research long-running coding agents and write source-backed notes plus a concise synthesis",
            tags=["research", "doc", "medium"],
            max_steps=6,
        ),
    ]
    large = [
        EvalCase(
            id="coding-large-package-plan",
            task=(
                "Build a large Python package project with "
                "pkg/models/user.py pkg/models/resource.py pkg/core/state.py pkg/core/engine.py "
                "pkg/systems/economy.py pkg/systems/building.py pkg/ai/planner.py "
                "pkg/io/save_load.py pkg/io/scenarios.py pkg/cli/main.py "
                "tests/test_core.py tests/test_io.py tests/test_integration.py"
            ),
            tags=["coding", "large", "package"],
            max_steps=8,
        ),
        EvalCase(
            id="data-report",
            task="Analyze a CSV-style dataset concept and produce a data report with schema, row count, sanity checks, and findings",
            tags=["data"],
            max_steps=5,
        ),
        EvalCase(
            id="ops-dry-run",
            task="Create an ops deployment dry-run plan with risks, rollback notes, approval gates, and evidence checklist",
            tags=["ops"],
            max_steps=5,
        ),
    ]
    stress = [
        EvalCase(
            id="stress-large-colony",
            task=(
                "Build a large dependency-free Python package named frontier_runtime with domain, core, ai, io, "
                "cli, persistence, scenario loading, and integration unittest coverage."
            ),
            tags=["coding", "stress", "large"],
            max_steps=16,
        ),
    ]
    suites = {
        "smoke": smoke,
        "small": smoke,
        "medium": [*smoke, *medium],
        "large": [*smoke, *medium, *large],
        "stress": [*smoke, *medium, *large, *stress],
    }
    return list(suites.get(suite, smoke))


class EvalSummary:
    def __init__(self, results: Iterable[EvalResult]):
        self.results = list(results)

    def to_metrics(self) -> Dict[str, int]:
        metrics: Dict[str, int] = {
            "cases": len(self.results),
            "completed": 0,
            "blocked": 0,
            "failed": 0,
            "teams": 0,
            "artifacts": 0,
            "steps": 0,
            "repair_tickets": 0,
            "gate_failures": 0,
            "false_done_risks": 0,
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_tool_results": 0,
        }
        for result in self.results:
            status = result.status.lower()
            if status == "completed":
                metrics["completed"] += 1
            elif status == "blocked":
                metrics["blocked"] += 1
            elif status == "failed":
                metrics["failed"] += 1
            metrics["teams"] += result.team_count
            metrics["artifacts"] += result.artifact_count
            metrics["steps"] += result.step_count
            metrics["repair_tickets"] += result.repair_ticket_count
            metrics["gate_failures"] += result.gate_failure_count
            metrics["false_done_risks"] += 1 if result.false_done_risk else 0
            metrics["llm_prompt_tokens"] += result.llm_prompt_tokens
            metrics["llm_completion_tokens"] += result.llm_completion_tokens
            metrics["llm_tool_results"] += result.llm_tool_results
        return metrics
