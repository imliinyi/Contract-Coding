"""Structured run monitor snapshots."""

from __future__ import annotations

import json
import os
from typing import Any, Dict


SENSITIVE_ENV_KEYS = {"API_KEY", "OPENAI_API_KEY", "BASE_URL", "API_VERSION", "OPENAI_API_BASE_URL"}


class RunMonitor:
    def __init__(self, engine):
        self.engine = engine

    def snapshot(self, run_id: str, write_file: bool = True) -> Dict[str, Any]:
        run_id = self.engine.resolve_run_id(run_id)
        status = self.engine.status(run_id)
        graph = self.engine.graph(run_id)
        run = status["run"]
        health = status["health"]
        steps = status.get("steps", [])
        llm = self._llm_summary(steps)
        snapshot = {
            "run": {"id": run.id, "status": run.status, "task": run.task},
            "task": status["task"].__dict__ if status.get("task") else None,
            "health": {
                "status": health.status,
                "replan_recommended": health.replan_recommended,
                "diagnostics": [self._diagnostic_record(diagnostic) for diagnostic in health.diagnostics[:20]],
            },
            "counts": self._counts(status),
            "ready_waves": graph.get("ready_waves", []),
            "blocked": graph.get("blocked", []),
            "teams": graph.get("teams", []),
            "gates": graph.get("gates", []),
            "repair_tickets": graph.get("repair_tickets", []),
            "repair_bundles": [
                ticket.get("repair_bundle")
                for ticket in graph.get("repair_tickets", [])
                if isinstance(ticket.get("repair_bundle"), dict) and ticket.get("repair_bundle")
            ],
            "llm_observability": llm,
            "report": self.engine.report(run.id, max_lines=20),
        }
        self._assert_no_secret_values(snapshot)
        if write_file:
            self._write(run.id, snapshot)
        return snapshot

    def _write(self, run_id: str, snapshot: Dict[str, Any]) -> str:
        workspace = os.path.abspath(self.engine.config.WORKSPACE_DIR)
        rel_path = os.path.join(".contractcoding", "monitor", f"{run_id}.json")
        path = os.path.join(workspace, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return rel_path.replace("\\", "/")

    @staticmethod
    def _counts(status: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {"items": {}, "teams": {}, "gates": {}, "tickets": {}}
        for item in status.get("work_items", []):
            out["items"][item.status] = out["items"].get(item.status, 0) + 1
        for team in status.get("scope_teams", []):
            out["teams"][team.status] = out["teams"].get(team.status, 0) + 1
        for gate in status.get("gates", []):
            out["gates"][gate.status] = out["gates"].get(gate.status, 0) + 1
        for ticket in status.get("repair_tickets", []):
            out["tickets"][ticket.status] = out["tickets"].get(ticket.status, 0) + 1
        return out

    @staticmethod
    def _llm_summary(steps) -> Dict[str, Any]:
        summary = {
            "observed_steps": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tool_intent_count": 0,
            "tool_result_count": 0,
            "timeout_count": 0,
            "empty_response_count": 0,
            "infra_failures": 0,
            "backends": {},
            "stop_reasons": {},
            "terminal_tools": {},
            "tool_iterations": 0,
        }
        for step in steps:
            output = step.output if isinstance(step.output, dict) else {}
            observed = dict(output.get("llm_observability", {}) or {})
            if not observed:
                continue
            summary["observed_steps"] += 1
            backend = str(observed.get("backend", "unknown"))
            summary["backends"][backend] = summary["backends"].get(backend, 0) + 1
            for key in ("prompt_tokens", "completion_tokens", "tool_intent_count", "tool_result_count", "timeout_count", "empty_response_count"):
                summary[key] += int(observed.get(key, 0) or 0)
            if observed.get("infra_failure"):
                summary["infra_failures"] += 1
            if observed.get("stop_reason"):
                reason = str(observed.get("stop_reason"))
                summary["stop_reasons"][reason] = summary["stop_reasons"].get(reason, 0) + 1
            if observed.get("terminal_tool"):
                tool = str(observed.get("terminal_tool"))
                summary["terminal_tools"][tool] = summary["terminal_tools"].get(tool, 0) + 1
            summary["tool_iterations"] += int(observed.get("tool_iterations", 0) or 0)
        return summary

    @staticmethod
    def _diagnostic_record(diagnostic) -> Dict[str, Any]:
        if hasattr(diagnostic, "to_record"):
            return diagnostic.to_record()
        return dict(getattr(diagnostic, "__dict__", {}))

    @staticmethod
    def _assert_no_secret_values(snapshot: Dict[str, Any]) -> None:
        rendered = json.dumps(snapshot, ensure_ascii=False)
        for key in SENSITIVE_ENV_KEYS:
            value = os.getenv(key)
            if value and value in rendered:
                raise ValueError(f"monitor snapshot attempted to include sensitive environment value: {key}")
