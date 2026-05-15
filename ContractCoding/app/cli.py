"""ContractCoding CLI.

Subcommands map directly to the registry-based runtime lifecycle:

    onboard       — register + freeze a PlanSpec (read from a JSON file)
    activate      — bootstrap a team's working_paper + task_ledger
    tick          — run one orchestration tick
    run           — orchestrate until idle (or `--max-ticks`)
    status        — print current plan + per-team task counts
    events        — tail the event log
    escalations   — list open escalations

Plan/team JSON examples live in `examples/`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .service import ContractCodingService
from ..config import Config
from ..contract.project import BoundedContext, IntentLedger, Invariant, PlanSpec
from ..memory.ledgers import TaskItem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ContractCoding: registry-based long-running multi-agent runtime",
    )
    parser.add_argument("--workspace", type=str, help="Workspace directory")
    parser.add_argument("--log-path", type=str, help="Path to agent log file")
    parser.add_argument("--offline", action="store_true", help="Use NullLLMPort (deterministic offline)")

    sub = parser.add_subparsers(dest="command")

    onboard = sub.add_parser("onboard", help="Register and freeze a plan from JSON")
    onboard.add_argument("plan_file", help="Path to plan JSON")
    onboard.add_argument("--no-freeze", action="store_true", help="Leave plan unfrozen")

    activate = sub.add_parser("activate", help="Bootstrap a team from JSON")
    activate.add_argument("team_file", help="Path to team-tasks JSON")

    tick = sub.add_parser("tick", help="Run a single orchestration tick")
    tick.add_argument("--max-per-team", type=int, default=1)

    run = sub.add_parser("run", help="Run orchestration until idle")
    run.add_argument("--max-ticks", type=int)
    run.add_argument("--max-per-team", type=int, default=1)

    status = sub.add_parser("status", help="Show plan + team status")
    status.add_argument("--json", action="store_true")

    events = sub.add_parser("events", help="Tail the event log")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--json", action="store_true")

    escalations = sub.add_parser("escalations", help="List open escalations")
    escalations.add_argument("--json", action="store_true")

    return parser


# ---------------------------------------------------------------------------
# JSON loaders
# ---------------------------------------------------------------------------


def _read_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _plan_from_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise plan JSON into kwargs for `service.onboard`."""
    bcs = [
        BoundedContext.from_mapping(b) if hasattr(BoundedContext, "from_mapping")
        else BoundedContext(**b)
        for b in payload.get("bounded_contexts", [])
    ]
    invariants = [
        Invariant.from_mapping(i) if hasattr(Invariant, "from_mapping")
        else Invariant(**i)
        for i in payload.get("invariants", []) or []
    ]
    return {
        "goal": str(payload.get("goal", "")),
        "bounded_contexts": bcs,
        "invariants": invariants,
        "acceptance_signals": list(payload.get("acceptance_signals", []) or []),
        "non_goals": list(payload.get("non_goals", []) or []),
        "assumptions": list(payload.get("assumptions", []) or []),
        "plan_version": str(payload.get("plan_version", "v1")),
    }


def _tasks_from_json(items: List[Dict[str, Any]]) -> List[TaskItem]:
    out: List[TaskItem] = []
    for raw in items:
        if hasattr(TaskItem, "from_mapping"):
            out.append(TaskItem.from_mapping(raw))
        else:
            out.append(TaskItem(**raw))
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.command:
        parser.print_help()
        return 0

    config = _config(args)
    service = ContractCodingService(config)
    offline = bool(getattr(args, "offline", False) or os.getenv("OFFLINE_LLM"))

    if args.command == "onboard":
        payload = _read_json(args.plan_file)
        kwargs = _plan_from_json(payload)
        plan = service.onboard(freeze=not args.no_freeze, **kwargs)
        print(service.json_dumps({
            "goal": plan.intent.goal,
            "frozen": plan.frozen,
            "teams": [c.team_id for c in plan.bounded_contexts],
        }))
        return 0

    if args.command == "activate":
        payload = _read_json(args.team_file)
        team_id = str(payload["team_id"])
        tasks = _tasks_from_json(payload.get("initial_tasks", []) or [])
        service.activate_team(team_id, initial_tasks=tasks)
        print(service.json_dumps({"team_id": team_id, "tasks_seeded": len(tasks)}))
        return 0

    if args.command == "tick":
        report = service.tick(offline=offline, max_per_team=args.max_per_team)
        print(service.json_dumps(report.__dict__))
        return 0

    if args.command == "run":
        reports = service.orchestrate(
            offline=offline,
            max_ticks=args.max_ticks,
            max_per_team=args.max_per_team,
        )
        totals = {
            "ticks": len(reports),
            "ran_tasks": sum(r.ran_tasks for r in reports),
            "approved": sum(r.approved for r in reports),
            "rejected": sum(r.rejected for r in reports),
            "spirals": sorted({t for r in reports for t in r.spiral_team_ids}),
        }
        print(service.json_dumps(totals))
        return 0

    if args.command == "status":
        payload = service.status()
        print(service.json_dumps(payload) if args.json else _format_status(payload))
        return 0

    if args.command == "events":
        payload = service.events(limit=args.limit)
        if args.json:
            print(service.json_dumps(payload))
        else:
            for evt in payload:
                print(f"{evt.get('created_at', 0):.0f} {evt.get('kind')} {evt.get('team_id')} {evt.get('payload')}")
        return 0

    if args.command == "escalations":
        payload = service.list_escalations()
        print(service.json_dumps(payload))
        return 0

    parser.print_help()
    return 1


def _format_status(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    plan = payload.get("plan")
    if plan is None:
        return "(no plan onboarded)"
    lines.append(f"Goal: {plan.get('goal')}")
    lines.append(f"Frozen: {plan.get('frozen')}  Version: {plan.get('version')}")
    lines.append("Teams:")
    for t in payload.get("teams", []):
        lines.append(
            f"  - {t['team_id']:<20} {t['tasks_done']}/{t['tasks_total']} done  | {t['purpose']}"
        )
    return "\n".join(lines)


def _config(args: argparse.Namespace) -> Config:
    values: Dict[str, Any] = {}
    if getattr(args, "workspace", None):
        values["WORKSPACE_DIR"] = args.workspace
    if getattr(args, "log_path", None):
        values["LOG_PATH"] = args.log_path
    return Config(**values)


if __name__ == "__main__":
    sys.exit(main())
