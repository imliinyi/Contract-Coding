from __future__ import annotations

import argparse
import json
import os
import sys

from ContractCoding.app.service import ContractCodingService
from ContractCoding.config import Config
from ContractCoding.quality.evals import EvalSuiteRunner, default_eval_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ContractCoding Runtime V5: Product Kernel long-running agent")
    parser.add_argument("--workspace", type=str, help="Workspace directory")
    parser.add_argument("--log-path", type=str, help="Path to agent log file")
    parser.add_argument("--max-steps", type=int, help="Maximum runtime steps")
    parser.add_argument("--offline", dest="global_offline", action="store_true", help="Use deterministic offline worker instead of OpenAI")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Plan and run a task")
    run.add_argument("task")
    run.add_argument("--max-steps", type=int)
    run.add_argument("--offline", action="store_true")

    resume = sub.add_parser("resume", help="Resume a run")
    resume.add_argument("run_id")
    resume.add_argument("--max-steps", type=int)
    resume.add_argument("--offline", action="store_true")

    status = sub.add_parser("status", help="Show run status")
    status.add_argument("run_id")
    status.add_argument("--json", action="store_true")

    graph = sub.add_parser("graph", help="Show run graph")
    graph.add_argument("run_id")
    graph.add_argument("--json", action="store_true")

    events = sub.add_parser("events", help="Show run events")
    events.add_argument("run_id")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--json", action="store_true")

    monitor = sub.add_parser("monitor", help="Show monitor snapshot")
    monitor.add_argument("run_id")
    monitor.add_argument("--json", action="store_true")

    eval_cmd = sub.add_parser("eval", help="Run built-in evals")
    eval_cmd.add_argument("--suite", choices=["smoke", "medium", "large", "stress"], default="smoke")
    eval_cmd.add_argument("--max-steps", type=int)
    eval_cmd.add_argument("--offline", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.command:
        parser.print_help()
        return
    config = _config(args)
    service = ContractCodingService(config)
    offline = bool(
        getattr(args, "global_offline", False)
        or getattr(args, "offline", False)
        or os.getenv("CONTRACTCODING_OFFLINE_WORKER")
    )

    if args.command == "run":
        result = service.run_auto(args.task, max_steps=args.max_steps or getattr(args, "max_steps", None), offline=offline)
        print(f"Run ID: {result.run_id}")
        print(f"Status: {result.status}")
        print(result.report)
        return
    if args.command == "resume":
        result = service.resume_run_auto(args.run_id, max_steps=args.max_steps, offline=offline)
        print(f"Run ID: {result.run_id}")
        print(f"Status: {result.status}")
        print(result.report)
        return
    if args.command == "status":
        payload = service.run_status(args.run_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if args.json else payload.get("report", ""))
        return
    if args.command == "graph":
        payload = service.run_graph(args.run_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.command == "events":
        payload = service.run_events(args.run_id, limit=args.limit)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for event in payload:
                print(f"{event.get('time')} {event.get('type')} {event.get('payload')}")
        return
    if args.command == "monitor":
        payload = service.run_monitor(args.run_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if args.json else payload.get("report", ""))
        return
    if args.command == "eval":
        runner = EvalSuiteRunner(service.run_engine)
        eval_offline = bool(args.offline or offline or os.getenv("RUN_OPENAI_E2E", "") not in {"1", "true", "yes"})
        results = runner.run(default_eval_cases(args.suite), max_steps=args.max_steps, offline=eval_offline)
        artifact = runner.write(args.suite, results)
        print(json.dumps({"artifact": artifact, "results": [result.to_record() for result in results]}, ensure_ascii=False, indent=2, sort_keys=True))
        return


def _config(args: argparse.Namespace) -> Config:
    values = {}
    if getattr(args, "workspace", None):
        values["WORKSPACE_DIR"] = args.workspace
    if getattr(args, "log_path", None):
        values["LOG_PATH"] = args.log_path
    return Config(**values)


if __name__ == "__main__":
    main()
