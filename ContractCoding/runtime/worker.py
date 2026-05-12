"""Feature-slice worker implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Dict, List

from ContractCoding.contract.spec import ContractSpec, FeatureSlice, WorkItem
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.knowledge.prompting import (
    bounded_worker_packet,
    system_prompt_for,
)
from ContractCoding.llm.factory import build_backend
from ContractCoding.tools.code_tool import build_run_code
from ContractCoding.tools.contract_tool import build_contract_tools
from ContractCoding.tools.file_tool import build_file_tools
from ContractCoding.tools.math_tool import solve_math_expression
from ContractCoding.tools.search_tool import search_web


@dataclass
class WorkerResult:
    ok: bool
    changed_files: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


class DeterministicWorker:
    """Offline worker used by tests and local dry runs.

    It intentionally writes simple real modules rather than placeholders. This
    lets the runtime be tested without spending API calls while still exercising
    planning, scheduling, gates, monitor, and repair transactions.
    """

    name = "deterministic"

    def execute(self, workspace_dir: str, contract: ContractSpec, item: WorkItem) -> WorkerResult:
        changed: List[str] = []
        for artifact in item.allowed_artifacts:
            path = os.path.join(workspace_dir, artifact)
            os.makedirs(os.path.dirname(path) or workspace_dir, exist_ok=True)
            content = self._content_for(artifact, contract)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            changed.append(artifact)
        return WorkerResult(ok=True, changed_files=changed, evidence=[f"wrote:{path}" for path in changed])

    def _content_for(self, artifact: str, contract: ContractSpec) -> str:
        if not artifact.endswith(".py"):
            return json.dumps({"generated_by": "ContractCoding", "artifact": artifact}, indent=2) + "\n"
        package = self._package_for(contract)
        if artifact.endswith("__init__.py"):
            return '"""Generated package."""\n\n__version__ = "0.1.0"\n\n\ndef describe() -> str:\n    return "generated package"\n'
        if artifact.startswith("tests/") or "/tests/" in artifact:
            return (
                "import importlib\n"
                "import unittest\n\n\n"
                "class GeneratedIntegrationTests(unittest.TestCase):\n"
                "    def test_package_imports(self):\n"
                f"        module = importlib.import_module('{package}')\n"
                "        self.assertTrue(hasattr(module, '__version__'))\n\n"
                "    def test_public_engine_shape(self):\n"
                f"        engine = importlib.import_module('{package}.core.engine')\n"
                "        self.assertEqual(engine.run_once({'value': 1})['status'], 'ok')\n\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            )
        canonical = self._canonical_types_for_artifact(artifact, contract)
        if canonical:
            return self._canonical_domain_source(canonical)
        if "/domain/" in artifact:
            return (
                "from dataclasses import dataclass, field\n"
                "from typing import Any, Dict\n\n\n"
                "@dataclass\n"
                "class Record:\n"
                "    name: str\n"
                "    values: Dict[str, Any] = field(default_factory=dict)\n\n\n"
                "def make_record(name: str, **values: Any) -> Record:\n"
                "    return Record(name=name, values=dict(values))\n"
            )
        if "/core/" in artifact:
            return (
                "from typing import Any, Dict\n\n\n"
                "def run_once(state: Dict[str, Any] | None = None) -> Dict[str, Any]:\n"
                "    return {'status': 'ok', 'state': dict(state or {})}\n\n\n"
                "class Engine:\n"
                "    def step(self, state: Dict[str, Any] | None = None) -> Dict[str, Any]:\n"
                "        return run_once(state)\n"
            )
        if "/io/" in artifact:
            return (
                "import json\n"
                "from typing import Any, Dict\n\n\n"
                "def dumps_state(state: Dict[str, Any]) -> str:\n"
                "    return json.dumps(state, sort_keys=True)\n\n\n"
                "def loads_state(payload: str) -> Dict[str, Any]:\n"
                "    return json.loads(payload)\n"
            )
        if "/interface/" in artifact or artifact.endswith("cli.py"):
            return (
                "import argparse\n"
                "import json\n\n\n"
                "def build_parser() -> argparse.ArgumentParser:\n"
                "    parser = argparse.ArgumentParser(description='Generated CLI')\n"
                "    parser.add_argument('--status', action='store_true')\n"
                "    return parser\n\n\n"
                "def main(argv=None) -> int:\n"
                "    args = build_parser().parse_args(argv)\n"
                "    if args.status:\n"
                "        print(json.dumps({'status': 'ok'}))\n"
                "    return 0\n\n\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit(main())\n"
            )
        return "def describe() -> str:\n    return 'generated artifact'\n"

    @staticmethod
    def _package_for(contract: ContractSpec) -> str:
        for artifact in contract.required_artifacts:
            parts = artifact.split("/")
            if len(parts) > 1 and parts[0].isidentifier() and parts[0] != "tests":
                return parts[0]
        return "generated_app"

    @staticmethod
    def _canonical_types_for_artifact(artifact: str, contract: ContractSpec) -> List[str]:
        owners = dict((contract.product_kernel.ontology or {}).get("canonical_type_owners", {}) or {})
        return [name for name, owner in owners.items() if owner == artifact]

    @staticmethod
    def _canonical_domain_source(type_names: List[str]) -> str:
        lines = [
            "from __future__ import annotations",
            "",
            "from dataclasses import dataclass",
            "from enum import Enum",
            "from typing import Any, Dict",
            "",
        ]
        if "StableIdentifier" in type_names:
            lines.extend(
                [
                    "@dataclass(frozen=True)",
                    "class StableIdentifier:",
                    "    id: str",
                    "",
                    "    def __post_init__(self) -> None:",
                    "        if not str(self.id).strip():",
                    "            raise ValueError('StableIdentifier.id must be non-empty')",
                    "",
                    "    def to_dict(self) -> Dict[str, str]:",
                    "        return {'id': self.id}",
                    "",
                    "    @classmethod",
                    "    def from_value(cls, value: Any) -> 'StableIdentifier':",
                    "        if isinstance(value, cls):",
                    "            return value",
                    "        if isinstance(value, dict):",
                    "            return cls(str(value.get('id', '')))",
                    "        return cls(str(value))",
                    "",
                ]
            )
        if "GridPoint" in type_names:
            lines.extend(
                [
                    "@dataclass(frozen=True)",
                    "class GridPoint:",
                    "    x: float",
                    "    y: float",
                    "",
                    "    def to_dict(self) -> Dict[str, float]:",
                    "        return {'x': float(self.x), 'y': float(self.y)}",
                    "",
                    "    @classmethod",
                    "    def from_value(cls, value: Any) -> 'GridPoint':",
                    "        if isinstance(value, cls):",
                    "            return value",
                    "        if isinstance(value, dict):",
                    "            return cls(float(value.get('x', 0)), float(value.get('y', 0)))",
                    "        x, y = value",
                    "        return cls(float(x), float(y))",
                    "",
                    "    def manhattan_distance(self, other: 'GridPoint') -> float:",
                    "        other = GridPoint.from_value(other)",
                    "        return abs(float(self.x) - float(other.x)) + abs(float(self.y) - float(other.y))",
                    "",
                ]
            )
        if "GeoPoint" in type_names:
            lines.extend(
                [
                    "@dataclass(frozen=True)",
                    "class GeoPoint:",
                    "    lat: float",
                    "    lon: float",
                    "",
                    "    def __post_init__(self) -> None:",
                    "        if not -90 <= float(self.lat) <= 90:",
                    "            raise ValueError('lat out of range')",
                    "        if not -180 <= float(self.lon) <= 180:",
                    "            raise ValueError('lon out of range')",
                    "",
                    "    def to_dict(self) -> Dict[str, float]:",
                    "        return {'lat': float(self.lat), 'lon': float(self.lon)}",
                    "",
                    "    @classmethod",
                    "    def from_value(cls, value: Any) -> 'GeoPoint':",
                    "        if isinstance(value, cls):",
                    "            return value",
                    "        return cls(float(value['lat']), float(value['lon']))",
                    "",
                ]
            )
        if "TaskStatus" in type_names:
            lines.extend(
                [
                    "class TaskStatus(str, Enum):",
                    "    PENDING = 'pending'",
                    "    IN_PROGRESS = 'in_progress'",
                    "    COMPLETE = 'complete'",
                    "    BLOCKED = 'blocked'",
                    "",
                ]
            )
        if "SpacecraftStatus" in type_names:
            lines.extend(
                [
                    "class SpacecraftStatus(str, Enum):",
                    "    IDLE = 'idle'",
                    "    ASSIGNED = 'assigned'",
                    "    IN_TRANSIT = 'in_transit'",
                    "    MAINTENANCE = 'maintenance'",
                    "",
                ]
            )
        exports = ", ".join(repr(name) for name in type_names)
        lines.extend([f"__all__ = [{exports}]", ""])
        return "\n".join(lines)


class OpenAIWorker:
    """OpenAI tool-calling worker for one Feature Slice or Repair Transaction."""

    name = "openai"

    def __init__(self, config):
        self.config = config
        self.backend = build_backend(config)

    def execute(self, workspace_dir: str, contract: ContractSpec, item: WorkItem) -> WorkerResult:
        feature_slice = contract.slice_by_id().get(item.slice_id)
        skills = ContextManager().skills_for_item(item, feature_slice)
        messages = self._messages(contract, item, feature_slice, skills)
        tools = [
            *build_file_tools(workspace_dir),
            build_run_code(workspace_dir),
            *build_contract_tools(workspace_dir, contract, item, feature_slice),
            search_web,
            solve_math_expression,
        ]
        self.backend.workspace_dir = workspace_dir
        self.backend.allowed_artifacts = list(item.allowed_artifacts)
        self.backend.allowed_conflict_keys = [f"artifact:{path}" for path in item.allowed_artifacts]
        self.backend.allowed_tools = [
            "file_tree",
            "read_file",
            "read_lines",
            "search_text",
            "inspect_symbol",
            "contract_snapshot",
            "inspect_module_api",
            "create_file",
            "replace_file",
            "write_file",
            "update_file_lines",
            "replace_symbol",
            "run_code",
            "run_public_flow",
            "submit_result",
            "report_blocker",
        ]
        self.backend.repair_diagnostics_text = json.dumps(item.diagnostics, ensure_ascii=False)
        response = self.backend.chat_with_tools(messages, tools)
        terminal = dict((response.raw or {}).get("terminal_result", {}) or {})
        raw_record = self._raw_record(response)
        if terminal.get("tool_name") == "report_blocker":
            return WorkerResult(
                ok=False,
                diagnostics=[terminal],
                raw=raw_record,
            )
        if (response.raw or {}).get("infra_failure"):
            return WorkerResult(
                ok=False,
                diagnostics=[
                    {
                        "code": "worker_infra_failure",
                        "slice_id": item.slice_id,
                        "work_item_id": item.id,
                        "failure_kind": str((response.raw or {}).get("failure_kind") or "unknown"),
                        "stop_reason": str((response.raw or {}).get("stop_reason") or ""),
                        "message": str((response.raw or {}).get("error") or "OpenAI worker failed before a valid submit_result."),
                        "attempts": list((response.raw or {}).get("attempts", []) or [])[-3:],
                    }
                ],
                raw=raw_record,
            )
        changed = [str(value) for value in terminal.get("changed_files", []) or []]
        evidence = [str(value) for value in terminal.get("evidence", []) or []]
        return WorkerResult(
            ok=not bool((response.raw or {}).get("infra_failure")),
            changed_files=changed,
            evidence=evidence or [response.content[:500]],
            raw=raw_record,
        )

    def _messages(
        self,
        contract: ContractSpec,
        item: WorkItem,
        feature_slice: FeatureSlice | None,
        skills: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, str]]:
        max_chars = int(getattr(getattr(self, "config", None), "CONTEXT_MAX_CHARS", 14000) or 14000)
        packet = bounded_worker_packet(
            contract,
            item,
            feature_slice,
            skills,
            max_chars=max_chars,
        )
        return [
            {
                "role": "system",
                "content": system_prompt_for(item),
            },
            {
                "role": "user",
                "content": json.dumps(
                    packet,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]

    @staticmethod
    def _raw_record(response) -> Dict[str, Any]:
        return {
            "backend": response.backend,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "raw": response.raw,
        }
