from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import sys
import time

from ContractCoding.app.service import ContractCodingService
from ContractCoding.config import Config
from ContractCoding.quality.evals import EvalSuiteRunner, default_real_task_eval_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ContractCoding: OpenAI-first contract-first long-running agent")
    parser.add_argument("--workspace", type=str, help="Workspace directory")
    parser.add_argument("--log-path", type=str, help="Path to agent log file")
    parser.add_argument("--backend", choices=["openai"], help="LLM backend to use")
    parser.add_argument("--max-steps", type=int, help="Maximum runtime steps for this invocation")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Plan and run a task")
    run_parser.add_argument("task", type=str, help="Task to run")
    run_parser.add_argument("--backend", dest="run_backend", choices=["openai"], help="LLM backend to use")
    run_parser.add_argument("--max-steps", dest="run_max_steps", type=int, help="Maximum runtime steps for this invocation")

    status_parser = subparsers.add_parser("status", help="Show task/run status")
    status_parser.add_argument("ref", type=str, help="Task id or run id")
    status_parser.add_argument("--json", action="store_true", help="Show raw status JSON")

    events_parser = subparsers.add_parser("events", help="Show task/run events")
    events_parser.add_argument("ref", type=str, help="Task id or run id")
    events_parser.add_argument("--limit", type=int, default=50)
    events_parser.add_argument("--follow", action="store_true")
    events_parser.add_argument("--human", action="store_true", help="Show human-readable events")
    events_parser.add_argument("--json", action="store_true", help="Show raw event JSON")

    graph_parser = subparsers.add_parser("graph", help="Show run dependency graph")
    graph_parser.add_argument("ref", type=str, help="Task id or run id")
    graph_parser.add_argument("--json", action="store_true", help="Show graph JSON")

    monitor_parser = subparsers.add_parser("monitor", help="Write and show a run monitor snapshot")
    monitor_parser.add_argument("ref", type=str, help="Task id or run id")
    monitor_parser.add_argument("--json", action="store_true", help="Show monitor JSON")

    eval_parser = subparsers.add_parser("eval", help="Run a built-in eval suite")
    eval_parser.add_argument("--suite", choices=["smoke", "small", "medium", "large", "stress"], default="smoke")
    eval_parser.add_argument("--backend", dest="eval_backend", choices=["openai"], help="LLM backend to use")
    eval_parser.add_argument("--max-steps", dest="eval_max_steps", type=int, help="Override max steps for each eval case")
    return parser


def build_config(args: argparse.Namespace) -> Config:
    config_kwargs = {}
    if args.workspace:
        config_kwargs["WORKSPACE_DIR"] = args.workspace
    if args.log_path:
        config_kwargs["LOG_PATH"] = args.log_path
    backend = getattr(args, "run_backend", None) or getattr(args, "eval_backend", None) or getattr(args, "backend", None)
    if backend:
        config_kwargs["LLM_BACKEND"] = backend
    return Config(**config_kwargs)


def normalize_argv(argv: list[str] | None = None) -> list[str]:
    return list(sys.argv[1:] if argv is None else argv)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))

    if not args.command:
        parser.print_help()
        return

    config = build_config(args)
    service = ContractCodingService(config)
    service.register_default_agents()

    if args.command == "run":
        max_steps = getattr(args, "run_max_steps", None)
        if max_steps is None:
            max_steps = getattr(args, "max_steps", None)
        run_id = _resolve_existing_run(service, args.task)
        if run_id:
            result = service.resume_run_auto(run_id, max_steps=max_steps)
        else:
            result = service.run_auto(args.task, max_steps=max_steps)
        print(f"Task ID: {result.task_id}")
        print(f"Run ID: {result.run_id}")
        print(f"Status: {result.status}")
        print(result.report)
        return

    if args.command == "status":
        if args.json:
            print(json.dumps(_status_to_jsonable(service.run_status(args.ref)), ensure_ascii=False, indent=2, sort_keys=True))
            return
        print(service.run_status_text(args.ref))
        return

    if args.command == "events":
        _print_events(service, args)
        return

    if args.command == "graph":
        graph = service.run_graph(args.ref)
        if args.json:
            print(json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "monitor":
        snapshot = service.run_monitor(args.ref, write_file=True)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(snapshot.get("report", ""))
        return

    if args.command == "eval":
        cases = default_real_task_eval_cases(args.suite)
        if args.eval_max_steps is not None:
            for case in cases:
                case.max_steps = args.eval_max_steps
        runner = EvalSuiteRunner(service.run_engine)
        results = runner.run_cases(cases, suite_id=args.suite)
        artifact = runner.write_results(args.suite, results)
        payload = {
            "suite": args.suite,
            "artifact": artifact,
            "results": [result.to_record() for result in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return


def _print_events(service: ContractCodingService, args: argparse.Namespace) -> None:
    seen = set()
    while True:
        if args.json:
            events = list(reversed(service.run_events(args.ref, limit=args.limit)))
            for event in events:
                if event.id in seen:
                    continue
                seen.add(event.id)
                print(
                    json.dumps(
                        {
                            "id": event.id,
                            "created_at": event.created_at,
                            "event_type": event.event_type,
                            "payload": event.payload,
                        },
                        ensure_ascii=False,
                    )
                )
        else:
            for line in service.run_events_human(args.ref, limit=args.limit):
                key = line.split(" ", 1)[0]
                if key in seen:
                    continue
                seen.add(key)
                print(line)
        if not args.follow:
            break
        time.sleep(2)


def _resolve_existing_run(service: ContractCodingService, value: str) -> str:
    try:
        return service.run_engine.resolve_run_id(value)
    except Exception:
        return service.run_engine.find_active_run_for_task(value)


def _status_to_jsonable(status: dict) -> dict:
    def record(value):
        if hasattr(value, "to_record"):
            return value.to_record()
        if is_dataclass(value):
            return asdict(value)
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return value

    return {
        "run": record(status["run"]),
        "task": record(status["task"]) if status.get("task") else None,
        "contract": record(status["contract"]) if status.get("contract") else None,
        "work_items": [record(item) for item in status.get("work_items", [])],
        "steps": [record(step) for step in status.get("steps", [])],
        "team_runs": [record(team) for team in status.get("team_runs", [])],
        "scope_teams": [record(team) for team in status.get("scope_teams", [])],
        "gates": [record(gate) for gate in status.get("gates", [])],
        "repair_tickets": [record(ticket) for ticket in status.get("repair_tickets", [])],
        "events": [record(event) for event in status.get("events", [])],
        "blocked": [record(blocked) for blocked in status.get("blocked", [])],
        "health": {
            "status": status["health"].status,
            "replan_recommended": status["health"].replan_recommended,
            "diagnostics": [record(diagnostic) for diagnostic in status["health"].diagnostics],
        },
    }
