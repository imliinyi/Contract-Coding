#!/usr/bin/env python3
"""Suggest narrow validation commands from changed paths."""

from __future__ import annotations

import sys
from pathlib import Path


def suggest(paths: list[str]) -> list[str]:
    normalized = [Path(path) for path in paths if path.strip()]
    if not normalized:
        return ["python3 -m unittest discover -s tests"]
    commands: list[str] = []
    test_paths = [str(path) for path in normalized if "test" in path.name and path.suffix == ".py"]
    if test_paths:
        commands.extend(f"python3 -m unittest {path}" for path in test_paths)
    if any(path.suffix == ".py" for path in normalized):
        commands.append("python3 -m unittest discover -s tests")
    if any(path.name in {"setup.py", "pyproject.toml"} for path in normalized):
        commands.append("python3 -m pip check")
    return commands or ["run the closest project-specific smoke check"]


def main(argv: list[str]) -> int:
    paths = argv[1:] or [line.strip() for line in sys.stdin if line.strip()]
    for command in suggest(paths):
        print(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

