from __future__ import annotations

import argparse

from ContractCoding.app.service import ContractCodingService
from ContractCoding.config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ContractCoding: Multi-Agent Collaboration Framework")
    parser.add_argument("--task", type=str, help="The task to execute")
    parser.add_argument("--train", action="store_true", help="Run in training mode")
    parser.add_argument("--workspace", type=str, help="Workspace directory for file tools")
    parser.add_argument("--log-path", type=str, help="Path to agent log file")
    parser.add_argument("--max-layers", type=int, help="Maximum orchestration layers")
    return parser


def build_config(args: argparse.Namespace) -> Config:
    config_kwargs = {}
    if args.workspace:
        config_kwargs["WORKSPACE_DIR"] = args.workspace
    if args.log_path:
        config_kwargs["LOG_PATH"] = args.log_path
    if args.max_layers is not None:
        config_kwargs["MAX_LAYERS"] = args.max_layers
    return Config(**config_kwargs)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = build_config(args)
    service = ContractCodingService(config)
    service.register_default_agents()

    if args.task:
        print(f"Starting ContractCoding with task: {args.task}")
        result = service.run(args.task)
        print("Final Result: None" if result is None else f"Final Result: {result.output}")
        return

    print("ContractCoding Engine initialized. Use --task to run a specific task.")
    parser.print_help()
