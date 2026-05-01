"""Project test command discovery for deterministic gates."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import sys
from typing import List


@dataclass
class TestCommand:
    name: str
    command: List[str]
    reason: str

    def to_record(self) -> dict:
        return {"name": self.name, "command": list(self.command), "reason": self.reason}


@dataclass
class TestDiscoveryResult:
    mode: str
    commands: List[TestCommand] = field(default_factory=list)

    def to_record(self) -> dict:
        return {"mode": self.mode, "commands": [command.to_record() for command in self.commands]}


class TestCommandDiscoverer:
    """Discover safe project-level test commands from repository manifests."""

    def __init__(self, workspace_dir: str, mode: str = "auto"):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.mode = (mode or "auto").strip().lower()

    def discover(self, requires_tests: bool = False) -> TestDiscoveryResult:
        if self.mode == "off":
            return TestDiscoveryResult(mode="off")
        commands: List[TestCommand] = []
        if self.mode in {"auto", "python-only"}:
            commands.extend(self._python_commands(requires_tests=requires_tests))
        if self.mode == "auto":
            commands.extend(self._node_commands())
            commands.extend(self._cargo_commands())
            commands.extend(self._go_commands())
        return TestDiscoveryResult(mode=self.mode, commands=self._dedupe(commands))

    def _python_commands(self, requires_tests: bool) -> List[TestCommand]:
        commands: List[TestCommand] = []
        has_pytest = self._exists("pytest.ini") or self._exists("pyproject.toml") or self._exists("tox.ini")
        has_tests = self._exists("tests") or any(name.startswith("test_") and name.endswith(".py") for name in os.listdir(self.workspace_dir))
        if has_pytest and has_tests:
            commands.append(TestCommand("pytest", [sys.executable, "-m", "pytest"], "pytest config/tests detected"))
        if has_tests or requires_tests:
            search_dir = "tests" if self._exists("tests") else "."
            commands.append(
                TestCommand(
                    "unittest",
                    [sys.executable, "-m", "unittest", "discover", "-s", search_dir, "-p", "test*.py", "-v"],
                    "python unittest discovery fallback",
                )
            )
        return commands

    def _node_commands(self) -> List[TestCommand]:
        package_path = os.path.join(self.workspace_dir, "package.json")
        if not os.path.exists(package_path):
            return []
        try:
            with open(package_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
        scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return [TestCommand("npm", ["npm", "test"], "package.json test script detected")]
        return []

    def _cargo_commands(self) -> List[TestCommand]:
        if self._exists("Cargo.toml"):
            return [TestCommand("cargo", ["cargo", "test"], "Cargo.toml detected")]
        return []

    def _go_commands(self) -> List[TestCommand]:
        if self._exists("go.mod"):
            return [TestCommand("go", ["go", "test", "./..."], "go.mod detected")]
        return []

    def _exists(self, rel_path: str) -> bool:
        return os.path.exists(os.path.join(self.workspace_dir, rel_path))

    @staticmethod
    def _dedupe(commands: List[TestCommand]) -> List[TestCommand]:
        out: List[TestCommand] = []
        seen = set()
        for command in commands:
            key = tuple(command.command)
            if key not in seen:
                seen.add(key)
                out.append(command)
        return out
