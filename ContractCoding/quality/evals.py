"""Small eval harness for Runtime V5."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time
from typing import Any, Dict, List

from ContractCoding.runtime.engine import RunEngine


@dataclass
class EvalCase:
    id: str
    task: str
    max_steps: int = 20


@dataclass
class EvalResult:
    case_id: str
    run_id: str
    status: str
    elapsed_seconds: float
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "metrics": dict(self.metrics),
        }


class EvalSuiteRunner:
    def __init__(self, engine: RunEngine):
        self.engine = engine

    def run(self, cases: List[EvalCase], max_steps: int | None = None, offline: bool = True) -> List[EvalResult]:
        results: List[EvalResult] = []
        for case in cases:
            started = time.perf_counter()
            result = self.engine.run(case.task, max_steps=max_steps or case.max_steps, offline=offline)
            snapshot = self.engine.status(result.run_id)
            results.append(
                EvalResult(
                    case_id=case.id,
                    run_id=result.run_id,
                    status=result.status,
                    elapsed_seconds=round(time.perf_counter() - started, 4),
                    metrics={
                        "slice_count": len(snapshot.get("slices", [])),
                        "item_count": len(snapshot.get("items", [])),
                        "team_count": len(snapshot.get("teams", [])),
                        "promotion_count": len(snapshot.get("promotions", [])),
                        "replan_count": len(snapshot.get("replans", [])),
                        "repair_transactions": len(snapshot.get("repair_transactions", [])),
                        "llm_telemetry": snapshot.get("llm_telemetry", {}),
                    },
                )
            )
        return results

    def write(self, suite_id: str, results: List[EvalResult]) -> str:
        path = os.path.join(self.engine.workspace_dir, ".contractcoding", "evals", f"{suite_id}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([result.to_record() for result in results], handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path


def default_eval_cases(suite: str = "smoke") -> List[EvalCase]:
    package_task = (
        "Build a dependency-free Python package named atlas_ops with atlas_ops/__init__.py, "
        "atlas_ops/domain/models.py, atlas_ops/core/engine.py, atlas_ops/io/storage.py, "
        "atlas_ops/interface/cli.py, and tests/test_integration.py."
    )
    if suite == "smoke":
        return [EvalCase("smoke-package", package_task, max_steps=20)]
    if suite == "medium":
        return [
            EvalCase(
                "medium-sim",
                package_task
                + " Also include atlas_ops/planning/policies.py, atlas_ops/ai/planner.py, tests/test_planning.py.",
                max_steps=30,
            )
        ]
    if suite == "large":
        artifact_list = [
            "nebula_colony/__init__.py",
            "nebula_colony/core/engine.py",
            "nebula_colony/interface/cli.py",
            *[f"nebula_colony/domain/model_{idx}.py" for idx in range(8)],
            *[f"nebula_colony/core/system_{idx}.py" for idx in range(6)],
            *[f"nebula_colony/planning/policy_{idx}.py" for idx in range(4)],
            *[f"nebula_colony/ai/planner_{idx}.py" for idx in range(4)],
            *[f"nebula_colony/io/storage_{idx}.py" for idx in range(4)],
            *[f"tests/test_kernel_{idx}.py" for idx in range(4)],
        ]
        artifacts = " ".join(artifact_list)
        return [EvalCase("large-package", f"Build a large package with {artifacts}", max_steps=60)]
    return [EvalCase("stress-package", package_task, max_steps=80)]
