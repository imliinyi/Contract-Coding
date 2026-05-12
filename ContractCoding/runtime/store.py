"""JSON run store for the rewritten long-running runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from typing import Any, Dict, List
from uuid import uuid4

from ContractCoding.contract.spec import ContractSpec


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class RunRecord:
    id: str
    task: str
    workspace: str
    status: str
    contract: ContractSpec
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    steps: int = 0
    final_diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "workspace": self.workspace,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": self.steps,
            "final_diagnostics": list(self.final_diagnostics),
            "contract": self.contract.to_record(),
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "RunRecord":
        payload = dict(payload or {})
        return cls(
            id=str(payload.get("id", "")),
            task=str(payload.get("task", "")),
            workspace=str(payload.get("workspace", "")),
            status=str(payload.get("status", "PENDING")),
            contract=ContractSpec.from_mapping(payload.get("contract", {})),
            created_at=str(payload.get("created_at", utc_now())),
            updated_at=str(payload.get("updated_at", utc_now())),
            steps=int(payload.get("steps", 0) or 0),
            final_diagnostics=list(payload.get("final_diagnostics", []) or []),
        )


class RunStore:
    def __init__(self, workspace_dir: str, store_path: str = ""):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.root = os.path.abspath(store_path) if store_path else os.path.join(self.workspace_dir, ".contractcoding")
        self.runs_dir = os.path.join(self.root, "runs")
        self.events_dir = os.path.join(self.root, "events")
        os.makedirs(self.runs_dir, exist_ok=True)
        os.makedirs(self.events_dir, exist_ok=True)

    def create_run(self, task: str, contract: ContractSpec) -> RunRecord:
        run = RunRecord(
            id=uuid4().hex,
            task=task,
            workspace=self.workspace_dir,
            status="RUNNING",
            contract=contract,
        )
        self.save(run)
        self.append_event(run.id, "run_created", {"task": task})
        return run

    def save(self, run: RunRecord) -> None:
        run.updated_at = utc_now()
        os.makedirs(self.runs_dir, exist_ok=True)
        with open(self._run_path(run.id), "w", encoding="utf-8") as handle:
            json.dump(run.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")

    def get(self, run_id: str) -> RunRecord:
        with open(self._run_path(run_id), "r", encoding="utf-8") as handle:
            return RunRecord.from_mapping(json.load(handle))

    def list_runs(self) -> List[RunRecord]:
        runs: List[RunRecord] = []
        for name in sorted(os.listdir(self.runs_dir)):
            if name.endswith(".json"):
                try:
                    runs.append(self.get(name[:-5]))
                except Exception:
                    continue
        return runs

    def append_event(self, run_id: str, event_type: str, payload: Dict[str, Any] | None = None) -> None:
        os.makedirs(self.events_dir, exist_ok=True)
        record = {"time": utc_now(), "type": event_type, "payload": payload or {}}
        with open(self._events_path(run_id), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def events(self, run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        path = self._events_path(run_id)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        return rows[-max(1, int(limit or 100)) :]

    def _run_path(self, run_id: str) -> str:
        return os.path.join(self.runs_dir, f"{run_id}.json")

    def _events_path(self, run_id: str) -> str:
        return os.path.join(self.events_dir, f"{run_id}.jsonl")
