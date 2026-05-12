"""Thin application service over Runtime V5."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ContractCoding.config import Config
from ContractCoding.runtime.engine import AutoRunResult, RunEngine


class ContractCodingService:
    def __init__(self, config: Config):
        self.config = config
        self.run_engine = RunEngine(config)

    def run_auto(self, task: str, max_steps: int | None = None, offline: bool = False) -> AutoRunResult:
        return self.run_engine.run(task, max_steps=max_steps, offline=offline)

    def resume_run_auto(self, run_id: str, max_steps: int | None = None, offline: bool = False) -> AutoRunResult:
        return self.run_engine.resume(run_id, max_steps=max_steps, offline=offline)

    def run_status(self, run_id: str) -> Dict[str, Any]:
        return self.run_engine.status(run_id)

    def run_status_text(self, run_id: str) -> str:
        return self.run_status(run_id).get("report", "")

    def run_graph(self, run_id: str) -> Dict[str, Any]:
        return self.run_engine.graph(run_id)

    def run_events(self, run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.run_engine.events(run_id, limit=limit)

    def run_monitor(self, run_id: str) -> Dict[str, Any]:
        return self.run_engine.status(run_id)

    @staticmethod
    def json_dumps(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
