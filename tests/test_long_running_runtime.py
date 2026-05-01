import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from ContractCoding.app.cli import build_config, build_parser, normalize_argv, _status_to_jsonable
from ContractCoding.agents.profile import AgentProfileRegistry
from ContractCoding.agents.prompt_builder import AgentPromptBuilder
from ContractCoding.agents.prompts import CORE_SYSTEM_PROMPT
from ContractCoding.config import Config
from ContractCoding.knowledge.manager import ContextManager, SkillSpec
from ContractCoding.contract.compiler import ContractCompiler
from ContractCoding.contract.planner import ContractDraftPlanner, ContractDraftReviewer, DraftPlanResult, PlanCritic
from ContractCoding.contract.spec import (
    ContractSpec,
    ContractValidationError,
    FinalGateSpec,
    InterfaceSpec,
    MilestoneSpec,
    PhaseContract,
    TeamGateSpec,
    WorkScope,
    load_contract_json,
)
from ContractCoding.contract.store import ContractFileStore
from ContractCoding.quality import (
    EvalCase,
    EvalSuiteRunner,
    EvalSummary,
    GateChecker,
    GateReviewParser,
    default_real_task_eval_cases,
)
from ContractCoding.llm.base import parse_tool_intents
from ContractCoding.llm.observability import response_observability
from ContractCoding.llm.openai_backend import OpenAIBackend, normalize_azure_endpoint
from ContractCoding.contract.work_item import WorkItem
from ContractCoding.runtime.engine import RunEngine
from ContractCoding.runtime.fsm import WorkItemStateMachine, normalize_work_item_status
from ContractCoding.runtime.gate_runner import GateRunner
from ContractCoding.runtime.health import FAILURE_HUMAN_REQUIRED, HealthMonitor
from ContractCoding.runtime.invariants import InvariantChecker
from ContractCoding.runtime.narrative import RunNarrativeBuilder
from ContractCoding.runtime.promotion import PromotionMetadataWriter
from ContractCoding.runtime.store import GateRecord, RunRecord, RunStore
from ContractCoding.runtime.team_executor import TeamExecutor
from ContractCoding.runtime.teams import DependencyImpactAnalyzer, TeamPlanner, TeamRuntime
from ContractCoding.runtime.test_discovery import TestCommandDiscoverer
from ContractCoding.runtime.test_strata import TestStrataAuditor
from ContractCoding.runtime.scheduler import Scheduler, TeamWave
from ContractCoding.execution.harness import TaskHarness
from ContractCoding.execution.workspace import get_current_workspace, workspace_scope
from ContractCoding.tools.file_tool import build_file_tools
from ContractCoding.tools.governor import ToolGovernor
from ContractCoding.utils.state import GeneralState


class LongRunningRuntimeTests(unittest.TestCase):
    def test_agent_profile_registry_resolves_canonical_roles(self):
        registry = AgentProfileRegistry()

        self.assertEqual(registry.get("Backend_Engineer").role_kind, "Implementation Worker")
        self.assertEqual(registry.resolve_agent_name("Implementation Worker", "coding"), "Backend_Engineer")
        self.assertEqual(registry.resolve_agent_name("Critic", "research"), "Critic")
        self.assertEqual(registry.resolve_agent_name("Evaluator", "eval"), "Evaluator")
        self.assertEqual(registry.resolve_agent_name("Unknown", "doc"), "Technical_Writer")

    def test_work_item_fsm_keeps_lifecycle_compact_and_blocks_bad_transition(self):
        fsm = WorkItemStateMachine()

        self.assertEqual(normalize_work_item_status("produced"), "DONE")
        self.assertTrue(fsm.can_transition("READY", "IN_PROGRESS").allowed)
        self.assertTrue(fsm.can_transition("DONE", "VERIFIED").allowed)
        self.assertFalse(fsm.can_transition("VERIFIED", "IN_PROGRESS").allowed)

    def test_gate_review_parser_prefers_json_verdicts(self):
        verdict = GateReviewParser().parse(
            {
                "output": (
                    '<gate_review>{"verdict":"blocked","block_reason":"invalid_tests","evidence":[],'
                    '"risks":["unknown behavior"]}</gate_review>'
                )
            },
            {"review_layer": "team", "allowed_block_reasons": ["invalid_tests"]},
        )

        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.verdict, "blocked")
        self.assertIn("invalid_tests", verdict.error)

    def test_tool_intent_parser_accepts_structured_adapter_variants(self):
        intents = parse_tool_intents(
            '<tool_intents>{"intents":[{"tool":"write_file","artifact":".contractcoding/interfaces/ai.json",'
            '"content":"{}","reason":"declare interface"}]}</tool_intents>'
        )

        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].name, "write_file")
        self.assertEqual(intents[0].arguments["path"], ".contractcoding/interfaces/ai.json")
        self.assertEqual(intents[0].arguments["content"], "{}")

    def test_placeholder_scan_allows_protocol_and_type_ellipsis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            package_dir = os.path.join(tmpdir, "pkg")
            os.makedirs(package_dir)
            artifact = os.path.join(package_dir, "ports.py")
            with open(artifact, "w", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            "from typing import Any, Mapping, Protocol, Tuple",
                            "",
                            "class StateFactory(Protocol):",
                            "    def __call__(self, data: Mapping[str, Any]) -> Any:",
                            "        ...",
                            "",
                            "TAGS: Tuple[str, ...] = ()",
                        ]
                    )
                )

            checker = InvariantChecker(tmpdir)

            self.assertEqual(checker._placeholder_hits(["pkg/ports.py"]), [])

    def test_file_tools_support_progressive_symbol_repair_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            path = os.path.join(tmpdir, "pkg", "mod.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            "class Counter:",
                            "    def value(self):",
                            "        return 1",
                            "",
                            "def helper():",
                            "    return 'old'",
                            "",
                        ]
                    )
                )

            tools = {tool.__name__: tool for tool in build_file_tools(tmpdir)}
            search_result = tools["search_text"](pattern="Counter", path="pkg")
            symbol_text = tools["inspect_symbol"](file_path="pkg/mod.py", symbol="Counter.value")
            replace_result = tools["replace_symbol"](
                file_path="pkg/mod.py",
                symbol="Counter.value",
                new_content="    def value(self):\n        return 2\n",
            )

            self.assertIn("pkg/mod.py:1", search_result)
            self.assertIn("def value", symbol_text)
            self.assertIn("Successfully replaced symbol", replace_result)
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("return 2", content)
            self.assertIn("def helper", content)

    def test_run_store_persists_ready_work_items_by_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            first = WorkItem(id="a", kind="doc", title="A", owner_profile="Technical_Writer", status="VERIFIED")
            second = WorkItem(
                id="b",
                kind="doc",
                title="B",
                owner_profile="Technical_Writer",
                depends_on=["a"],
                status="TODO",
            )

            run_id = store.create_run("demo", tmpdir, [first, second])
            ready = store.ready_work_items(run_id)

            self.assertEqual([item.id for item in ready], ["b"])
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "runs.sqlite")))

    def test_contract_compiler_rejects_dependency_cycles(self):
        compiler = ContractCompiler()

        with self.assertRaises(ContractValidationError):
            compiler.compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "root", "type": "root"}],
                    "work_items": [
                        {
                            "id": "a",
                            "kind": "doc",
                            "title": "A",
                            "owner_profile": "Technical_Writer",
                            "scope_id": "root",
                            "depends_on": ["b"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "b",
                            "kind": "doc",
                            "title": "B",
                            "owner_profile": "Technical_Writer",
                            "scope_id": "root",
                            "depends_on": ["a"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                },
            )

    def test_contract_compiler_auto_plans_parallel_coding_work_from_task(self):
        contract = ContractCompiler().compile("Create two independent Python utility modules")
        coding_ids = {item.id for item in contract.work_items if item.kind == "coding"}
        gate_by_scope = {gate.scope_id: gate for gate in contract.team_gates}

        self.assertEqual(contract.metadata["planner"], "deterministic-mvp")
        self.assertEqual(coding_ids, {"coding:math_tools.py", "coding:text_tools.py"})
        self.assertIn("utils", gate_by_scope)
        self.assertIsNotNone(contract.final_gate)
        self.assertFalse(any(item.kind == "eval" for item in contract.work_items))
        self.assertTrue(all(item.status == "READY" for item in contract.work_items if item.kind == "coding"))
        self.assertEqual({item.scope_id for item in contract.work_items if item.kind == "coding"}, {"utils"})
        self.assertEqual(
            {tuple(item.conflict_keys) for item in contract.work_items if item.kind == "coding"},
            {("artifact:math_tools.py",), ("artifact:text_tools.py",)},
        )

        utility = ContractCompiler().compile("Create a small Python CLI utility")
        utility_gate = {gate.scope_id: gate for gate in utility.team_gates}["utils"]
        self.assertEqual(utility_gate.test_artifacts, ["test_utils.py"])
        self.assertIn("test_utils.py", utility_gate.test_plan["test_artifacts"])
        self.assertNotIn("gravity", json.dumps(utility_gate.test_plan))

    def test_llm_draft_planner_is_guarded_by_contract_compiler(self):
        class FakeDraftPlanner:
            def propose(self, goal):
                return DraftPlanResult(
                    backend="fake",
                    draft={
                        "goals": [goal],
                        "work_scopes": [{"id": "docs", "type": "doc"}],
                        "work_items": [
                            {
                                "id": "doc:summary",
                                "kind": "doc",
                                "title": "Summary",
                                "owner_profile": "Technical_Writer",
                                "scope_id": "docs",
                                "status": "RUNNING",
                                "evidence": ["runtime pollution should not survive contract JSON"],
                                "target_artifacts": [".contractcoding/output.md"],
                                "acceptance_criteria": ["summary is complete"],
                            }
                        ],
                        "acceptance_criteria": ["done"],
                    },
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                LLM_PLANNER_ENABLED=True,
            )
            engine = RunEngine(config=config, store=RunStore.for_workspace(tmpdir), draft_planner=FakeDraftPlanner())
            contract = engine.plan("Write a concise runtime summary", write_files=False)
            record = contract.to_record()

            self.assertIn("llm_draft_planner", contract.metadata["planning_pipeline"])
            self.assertIn("docs", {gate.scope_id for gate in contract.team_gates})
            self.assertIsNotNone(contract.final_gate)
            self.assertFalse(any("status" in item or "evidence" in item for item in record["work_items"]))

    def test_contract_draft_reviewer_rejects_unschedulable_llm_draft(self):
        reviewer = ContractDraftReviewer()
        result = reviewer.review(
            {
                "goals": ["demo"],
                "work_scopes": [{"id": "core", "type": "code_module"}],
                "work_items": [
                    {
                        "id": "a",
                        "kind": "coding",
                        "scope_id": "missing",
                        "depends_on": ["b"],
                        "acceptance_criteria": ["done"],
                    },
                    {
                        "id": "b",
                        "kind": "coding",
                        "scope_id": "core",
                        "depends_on": ["a"],
                        "acceptance_criteria": ["done"],
                    },
                ],
            }
        )

        self.assertFalse(result.accepted)
        rendered = " ".join(result.errors)
        self.assertIn("unknown scope", rendered)
        self.assertIn("dependency cycle", rendered)

    def test_llm_draft_reviewer_fallbacks_to_deterministic_contract(self):
        class BadDraftPlanner:
            def propose(self, goal):
                return DraftPlanResult(
                    backend="fake",
                    draft={
                        "goals": [goal],
                        "work_scopes": [{"id": "core", "type": "code_module"}],
                        "work_items": [
                            {
                                "id": "bad",
                                "kind": "coding",
                                "scope_id": "ghost",
                                "acceptance_criteria": ["done"],
                            }
                        ],
                    },
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                LLM_PLANNER_ENABLED=True,
            )
            engine = RunEngine(config=config, store=RunStore.for_workspace(tmpdir), draft_planner=BadDraftPlanner())
            contract = engine.plan("Implement app.py", write_files=False)

            self.assertEqual(contract.metadata["planner"], "deterministic-mvp")
            self.assertNotIn("llm_draft_planner", contract.metadata.get("planning_pipeline", []))
            self.assertEqual([item.target_artifacts[0] for item in contract.work_items if item.kind == "coding"], ["app.py"])

    def test_contract_draft_planner_extracts_tagged_json(self):
        payload = ContractDraftPlanner.extract_draft(
            '<contract_draft>{"goals":["demo"],"work_scopes":[],"work_items":[]}</contract_draft>'
        )

        self.assertEqual(payload["goals"], ["demo"])

    def test_contract_compiler_auto_plans_explicit_files_from_task(self):
        contract = ContractCompiler().compile("Implement alpha.py and beta.py")

        coding_targets = [item.target_artifacts[0] for item in contract.work_items if item.kind == "coding"]
        self.assertEqual(coding_targets, ["alpha.py", "beta.py"])
        self.assertIn("app", {gate.scope_id for gate in contract.team_gates})
        self.assertIsNotNone(contract.final_gate)

        punctuated = ContractCompiler().compile("Implement alpha.py, beta.py, and test_alpha.py.")
        punctuated_targets = [item.target_artifacts[0] for item in punctuated.work_items if item.kind == "coding"]
        self.assertEqual(punctuated_targets, ["alpha.py", "beta.py"])
        gate = {gate.scope_id: gate for gate in punctuated.team_gates}["app"]
        self.assertEqual(gate.test_artifacts, ["test_alpha.py"])
        self.assertTrue(punctuated.final_gate.requires_tests)

    def test_contract_compiler_prioritizes_python_package_over_domain_migration_word(self):
        task = (
            "Build a large dependency-free Python package named chronicle_forge. "
            "Users can define fictional societies, resources, migrations, conflicts, discoveries, "
            "run deterministic simulations, save and load scenarios, use a CLI, and run unittest tests."
        )

        contract = ContractCompiler().compile(task)

        self.assertEqual(contract.metadata["delivery_type"], "coding")
        self.assertTrue(any(item.kind == "coding" for item in contract.work_items))
        self.assertFalse(any(item.kind == "ops" for item in contract.work_items))
        self.assertTrue(
            any(path.startswith("chronicle_forge/") for item in contract.work_items for path in item.target_artifacts)
        )
        self.assertTrue(contract.final_gate.python_artifacts)

    def test_contract_compiler_auto_plans_game_with_api_boundaries(self):
        contract = ContractCompiler().compile("Create a Python terminal Connect Four game")
        items = {item.target_artifacts[0]: item for item in contract.work_items if item.kind == "coding"}

        self.assertEqual(
            set(items),
            {"game_engine.py", "ai_player.py", "main.py"},
        )
        self.assertEqual(items["main.py"].depends_on, ["coding:game_engine.py", "coding:ai_player.py"])
        self.assertEqual(items["main.py"].dependency_policy, "interface")
        self.assertTrue(items["game_engine.py"].provided_interfaces)
        self.assertTrue(items["main.py"].required_interfaces)
        game_gate = {gate.scope_id: gate for gate in contract.team_gates}["game"]
        self.assertEqual(game_gate.test_artifacts, ["test_game_engine.py"])
        self.assertEqual(game_gate.test_plan["required_public_interfaces"], ["game_engine.py", "ai_player.py", "main.py"])
        self.assertTrue(any("must not duplicate" in criterion for criterion in items["main.py"].acceptance_criteria))
        self.assertEqual(contract.metadata["planning_profile"]["complexity"], "medium")
        self.assertIn("test_game_engine.py", contract.final_gate.required_artifacts)

    def test_large_project_planner_builds_multi_scope_contract_and_final_gate(self):
        files = [
            "stellar_outpost/__init__.py",
            "stellar_outpost/models/resources.py",
            "stellar_outpost/models/citizens.py",
            "stellar_outpost/models/buildings.py",
            "stellar_outpost/models/tech.py",
            "stellar_outpost/core/state.py",
            "stellar_outpost/core/engine.py",
            "stellar_outpost/core/rules.py",
            "stellar_outpost/core/events.py",
            "stellar_outpost/core/scheduler.py",
            "stellar_outpost/systems/economy.py",
            "stellar_outpost/systems/construction.py",
            "stellar_outpost/systems/exploration.py",
            "stellar_outpost/systems/combat.py",
            "stellar_outpost/systems/research.py",
            "stellar_outpost/ai/opponent.py",
            "stellar_outpost/ai/planner.py",
            "stellar_outpost/io/save_load.py",
            "stellar_outpost/io/scenarios.py",
            "stellar_outpost/cli/main.py",
            "tests/test_models.py",
            "tests/test_core.py",
            "tests/test_systems.py",
            "tests/test_ai.py",
            "tests/test_io.py",
            "tests/test_cli.py",
            "tests/test_integration.py",
            "tests/test_regression.py",
        ]

        contract = ContractCompiler().compile("Build a large Python package project with " + " ".join(files))
        scope_ids = {scope.id for scope in contract.work_scopes}
        gate_by_scope = {gate.scope_id: gate for gate in contract.team_gates}

        self.assertEqual(contract.metadata["planner"], "large-project-deterministic")
        self.assertGreaterEqual(len(scope_ids - {"root", "integration"}), 6)
        self.assertTrue({"package", "domain", "core", "ai", "io", "interface"}.issubset(scope_ids))
        self.assertNotIn("tests", scope_ids)
        self.assertFalse(any(scope_id.endswith(".py") for scope_id in scope_ids))
        self.assertFalse(any(item.kind == "eval" or item.id.startswith(("test:", "verify:")) for item in contract.work_items))
        self.assertIn("core", gate_by_scope)
        self.assertIn("domain", gate_by_scope)
        self.assertIn("tests/test_core.py", gate_by_scope["core"].test_artifacts)
        self.assertFalse("ConnectFour" in json.dumps(gate_by_scope["core"].test_plan))
        self.assertFalse("game_engine.py" in json.dumps(gate_by_scope["core"].test_plan))
        self.assertIn("stellar_outpost/core/engine.py", gate_by_scope["core"].test_plan["required_public_interfaces"])
        self.assertIsNotNone(contract.final_gate)
        self.assertIn("tests/test_integration.py", contract.final_gate.required_artifacts)
        self.assertNotIn("tests/test_integration.py", {artifact for gate in contract.team_gates for artifact in gate.test_artifacts})
        behavior = contract.final_gate.product_behavior
        self.assertIn("cli_blackbox", behavior.get("capabilities", []))
        self.assertIn("planning", behavior.get("capabilities", []))
        self.assertTrue(
            any(
                len(command.get("argv", [])) >= 3
                and command.get("argv", [])[2] == "stellar_outpost.cli.main"
                for command in behavior.get("blackbox_commands", [])
            )
        )

        record = contract.to_record()
        self.assertFalse(any("status" in item or "evidence" in item for item in record["work_items"]))
        self.assertIn("team_gates", record)
        self.assertIn("final_gate", record)
        self.assertIn("autonomy_guardrails", record["execution_policy"])
        self.assertGreater(
            record["execution_policy"]["autonomy_guardrails"]["test_repair_limit"],
            record["execution_policy"]["autonomy_guardrails"]["item_repair_limit"],
        )

    def test_planner_fidelity_preserves_explicit_planning_scope(self):
        files = [
            "atlas_ops/__init__.py",
            "atlas_ops/domain/assets.py",
            "atlas_ops/domain/orders.py",
            "atlas_ops/core/dispatch.py",
            "atlas_ops/core/constraints.py",
            "atlas_ops/planning/planner.py",
            "atlas_ops/planning/policies.py",
            "atlas_ops/planning/heuristics.py",
            "atlas_ops/io/scenarios.py",
            "atlas_ops/io/save_load.py",
            "atlas_ops/interface/cli.py",
            "atlas_ops/interface/repl.py",
            "tests/test_planning.py",
            "tests/test_integration.py",
        ]

        contract = ContractCompiler().compile(
            "Build a large emergency logistics planning toolkit with " + " ".join(files)
        )
        scope_ids = {scope.id for scope in contract.work_scopes}
        planning_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == "planning" and item.id.startswith("implement:planning")
            for artifact in item.target_artifacts
        ]
        serialized = json.dumps(contract.to_record())

        self.assertIn("planning", scope_ids)
        self.assertNotIn("ai", scope_ids)
        self.assertIn("atlas_ops/planning/planner.py", planning_artifacts)
        self.assertIn("planner_fidelity_guard", contract.metadata["planning_pipeline"])
        self.assertNotIn("ColonyRepl", serialized)

    def test_runtime_control_plane_delays_interface_gate_until_dependency_scopes(self):
        files = [
            "rulegrid/__init__.py",
            "rulegrid/domain/cell.py",
            "rulegrid/domain/grid.py",
            "rulegrid/domain/rules.py",
            "rulegrid/core/engine.py",
            "rulegrid/core/history.py",
            "rulegrid/core/patterns.py",
            "rulegrid/io/text_format.py",
            "rulegrid/io/json_format.py",
            "rulegrid/cli.py",
            "tests/test_domain.py",
            "tests/test_engine.py",
            "tests/test_io.py",
            "tests/test_cli.py",
        ]

        contract = ContractCompiler().compile("Build a medium Python package project with " + " ".join(files))
        interface_item = next(item for item in contract.work_items if item.id == "implement:interface")
        phase_order = [phase.phase_id for phase in contract.phase_plan]
        interface_gate = next(gate for gate in contract.team_gates if gate.scope_id == "interface")

        self.assertEqual(interface_item.inputs["phase_id"], "feature:interface")
        self.assertLess(phase_order.index("feature:simulation"), phase_order.index("feature:interface"))
        self.assertLess(phase_order.index("feature:planning_io"), phase_order.index("feature:interface"))
        self.assertTrue({"package", "domain", "core", "io"}.issubset(set(interface_gate.test_plan["dependency_scope_ids"])))
        self.assertEqual(interface_gate.test_plan["test_strata"], "scope_local")
        self.assertEqual(interface_gate.test_plan["gate_depth"], "smoke")
        self.assertNotIn("scope_tests", interface_gate.deterministic_checks)

    def test_large_project_uses_functional_scale_without_loc_gate(self):
        files = [
            "nebula_colony/__init__.py",
            "nebula_colony/domain/resources.py",
            "nebula_colony/domain/citizens.py",
            "nebula_colony/domain/buildings.py",
            "nebula_colony/domain/technology.py",
            "nebula_colony/core/state.py",
            "nebula_colony/core/rules.py",
            "nebula_colony/core/engine.py",
            "nebula_colony/core/scheduler.py",
            "nebula_colony/core/simulation.py",
            "nebula_colony/systems/economy.py",
            "nebula_colony/systems/construction.py",
            "nebula_colony/systems/research.py",
            "nebula_colony/systems/exploration.py",
            "nebula_colony/ai/governor.py",
            "nebula_colony/ai/planner.py",
            "nebula_colony/io/save_load.py",
            "nebula_colony/io/scenarios.py",
            "nebula_colony/interface/cli.py",
            "nebula_colony/interface/repl.py",
            "tests/test_domain.py",
            "tests/test_core.py",
            "tests/test_systems.py",
            "tests/test_ai.py",
            "tests/test_io.py",
            "tests/test_cli.py",
            "tests/test_integration.py",
        ]

        contract = ContractCompiler().compile(
            "Build a very large Python package project with at least 20 files and at least 15000 LOC. "
            + " ".join(files)
        )
        record = contract.to_record()
        final_record = record["final_gate"]

        self.assertGreaterEqual(len(contract.final_gate.required_artifacts), 20)
        self.assertNotIn("loc_soft_min", final_record)
        self.assertNotIn("loc_required_min", final_record)
        self.assertTrue(contract.final_gate.final_acceptance_scenarios)
        self.assertEqual(contract.owner_hints["nebula_colony/interface/repl.py"], "interface")
        self.assertEqual(contract.to_record()["owner_hints"]["nebula_colony/interface/repl.py"], "interface")
        interface_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == "interface" and item.id.startswith("implement:interface")
            for artifact in item.target_artifacts
        ]
        self.assertIn("nebula_colony/interface/repl.py", interface_artifacts)
        core_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == "core" and item.id.startswith("implement:core")
            for artifact in item.target_artifacts
        ]
        self.assertNotIn("nebula_colony/interface/repl.py", core_artifacts)

        non_empty_contract = ContractCompiler().compile(
            "Build a very large Python package with at least 15000 non-empty lines of code. "
            + " ".join(files)
        )
        self.assertNotIn("loc_required_min", non_empty_contract.to_record()["final_gate"])
        meaningful_contract = ContractCompiler().compile(
            "Build a very large Python package with at least 15000 meaningful non-empty lines "
            "of production-quality code and tests without padding. "
            + " ".join(files)
        )
        self.assertNotIn("loc_required_min", meaningful_contract.to_record()["final_gate"])

    def test_status_artifact_coverage_uses_final_gate_required_artifacts(self):
        with tempfile.TemporaryDirectory() as workspace:
            os.makedirs(os.path.join(workspace, "pkg"), exist_ok=True)
            with open(os.path.join(workspace, "pkg", "a.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            run = RunRecord(
                id="run-1",
                task="demo",
                status="RUNNING",
                workspace_dir=workspace,
                created_at="now",
                updated_at="now",
                metadata={},
            )
            gate = GateRecord(
                run_id="run-1",
                gate_id="final",
                gate_type="final",
                scope_id="integration",
                status="PENDING",
                evidence=[],
                metadata={"spec": {"required_artifacts": ["pkg/a.py", "tests/test_a.py"]}},
                created_at="now",
                updated_at="now",
            )

            report = RunNarrativeBuilder().build_report(
                run=run,
                items=[],
                steps=[],
                team_runs=[],
                waves=[],
                health=None,
                gates=[gate],
                max_lines=20,
            )

        self.assertIn(
            "Artifact coverage: required=2, generated_sandbox_or_main=1, promoted_main=1, promoted_python_loc=1",
            report,
        )
        self.assertIn("missing=tests/test_a.py", report)

    def test_large_project_splits_large_functional_scope_into_team_batches(self):
        files = [
            "nebula_colony/__init__.py",
            "nebula_colony/domain/resources.py",
            "nebula_colony/domain/citizens.py",
            "nebula_colony/domain/buildings.py",
            "nebula_colony/domain/technology.py",
            "nebula_colony/core/state.py",
            "nebula_colony/core/rules.py",
            "nebula_colony/core/engine.py",
            "nebula_colony/core/scheduler.py",
            "nebula_colony/core/simulation.py",
            "nebula_colony/systems/economy.py",
            "nebula_colony/systems/construction.py",
            "nebula_colony/systems/research.py",
            "nebula_colony/systems/exploration.py",
            "nebula_colony/ai/governor.py",
            "nebula_colony/ai/planner.py",
            "nebula_colony/io/save_load.py",
            "nebula_colony/io/scenarios.py",
            "nebula_colony/interface/cli.py",
            "nebula_colony/interface/repl.py",
            "tests/test_domain.py",
            "tests/test_core.py",
            "tests/test_systems.py",
            "tests/test_ai.py",
            "tests/test_io.py",
            "tests/test_cli.py",
            "tests/test_integration.py",
        ]

        contract = ContractCompiler().compile(
            "Build a very large Python package project with at least 20 files and at least 15000 LOC. "
            + " ".join(files)
        )
        core_items = [
            item
            for item in contract.work_items
            if item.scope_id == "core" and item.id.startswith("implement:core")
        ]
        core_artifacts = [
            artifact
            for item in core_items
            for artifact in item.target_artifacts
        ]

        self.assertGreaterEqual(len(core_items), 2)
        self.assertTrue(all(item.inputs.get("team_batch") for item in core_items))
        self.assertEqual(sorted(core_artifacts), sorted([
            "nebula_colony/core/state.py",
            "nebula_colony/core/rules.py",
            "nebula_colony/core/engine.py",
            "nebula_colony/core/scheduler.py",
            "nebula_colony/core/simulation.py",
            "nebula_colony/systems/economy.py",
            "nebula_colony/systems/construction.py",
            "nebula_colony/systems/research.py",
            "nebula_colony/systems/exploration.py",
        ]))
        io_items = [
            item
            for item in contract.work_items
            if item.scope_id == "io" and item.id.startswith("implement:io")
        ]
        interface_items = [
            item
            for item in contract.work_items
            if item.scope_id == "interface" and item.id.startswith("implement:interface")
        ]
        self.assertGreaterEqual(len(io_items), 2)
        self.assertGreaterEqual(len(interface_items), 2)
        self.assertTrue(any(item.inputs.get("team_batch", {}).get("id") == "persistence" for item in io_items))
        self.assertTrue(any(item.inputs.get("team_batch", {}).get("id") == "scenarios" for item in io_items))
        self.assertFalse(any(scope.id.endswith(".py") for scope in contract.work_scopes))
        self.assertFalse(any("loc" in " ".join(item.acceptance_criteria).lower() for item in core_items))
        self.assertFalse(any(item.inputs.get("project_size_guidance") for item in core_items))
        self.assertFalse(any("team_loc" in json.dumps(item.inputs) for item in core_items))

    def test_large_project_scheduler_runs_interface_and_scope_waves_in_parallel(self):
        files = [
            "pkg/models/user.py",
            "pkg/models/resource.py",
            "pkg/core/state.py",
            "pkg/core/engine.py",
            "pkg/systems/economy.py",
            "pkg/systems/building.py",
            "pkg/ai/planner.py",
            "pkg/io/save_load.py",
            "pkg/io/scenarios.py",
            "pkg/cli/main.py",
            "tests/test_core.py",
            "tests/test_io.py",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile("Build a large Python package project with " + " ".join(files))
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)

            first_waves = Scheduler(store).next_wave(run_id)
            self.assertGreaterEqual(len(first_waves), 4)
            self.assertTrue(all(any(item.id.startswith("scaffold:") for item in wave.items) for wave in first_waves))

            for item in store.list_work_items(run_id):
                if item.id.startswith("scaffold:"):
                    store.update_work_item_status(run_id, item.id, "DONE")
                    store.update_work_item_status(run_id, item.id, "VERIFIED")

            second_waves = Scheduler(store).next_wave(run_id)
            self.assertTrue(all(any(item.id.startswith("interface:") for item in wave.items) for wave in second_waves))

            for item in store.list_work_items(run_id):
                if item.id.startswith("interface:"):
                    store.update_work_item_status(run_id, item.id, "DONE")
                    store.update_work_item_status(run_id, item.id, "VERIFIED")
            store.update_gate_status(run_id, "phase:vertical_slice", "PASSED", evidence=["vertical slice passed"])

            third_waves = Scheduler(store).next_wave(run_id)
            scopes = {wave.scope.id for wave in third_waves}
            implementation_items = {
                item.id
                for wave in third_waves
                for item in wave.items
                if wave.wave_kind == "implementation"
            }

            self.assertGreaterEqual(len(scopes), 2)
            self.assertTrue({"domain", "core"}.issubset(scopes))
            self.assertTrue(any(item_id.startswith("implement:domain") for item_id in implementation_items))
            self.assertTrue(any(item_id.startswith("implement:core") for item_id in implementation_items))
            self.assertFalse(any(item_id.startswith("verify:") for item_id in implementation_items))
            self.assertTrue(any(wave.parallel_reason or wave.serial_reason for wave in third_waves))

    def test_scheduler_preserves_runtime_inputs_for_repair_packets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build a Python package project with pkg/core/engine.py pkg/core/rules.py "
                "pkg/core/state.py pkg/core/scheduler.py pkg/core/simulation.py tests/test_core.py"
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)

            for item in store.list_work_items(run_id):
                if item.id.startswith(("scaffold:", "interface:")):
                    store.update_work_item_status(run_id, item.id, "DONE")
                    store.update_work_item_status(run_id, item.id, "VERIFIED")
            store.update_gate_status(run_id, "phase:vertical_slice", "PASSED", evidence=["vertical slice passed"])

            core_item = next(
                item
                for item in store.list_work_items(run_id)
                if item.scope_id == "core" and item.id.startswith("implement:core")
            )
            payload = core_item.to_record()
            payload["status"] = "READY"
            payload["inputs"] = {
                **payload.get("inputs", {}),
                "latest_diagnostic": {
                    "gate_id": "team:core",
                    "scope_id": "core",
                    "failure_kind": "unittest_assertion",
                    "failing_test": "test_engine_turns",
                    "affected_artifacts": ["pkg/core/engine.py"],
                    "repair_instruction": "Fix deterministic turn behavior.",
                },
            }
            store.upsert_work_item(run_id, WorkItem.from_mapping(payload))

            waves = Scheduler(store).next_wave(run_id)
            dispatched = [
                item
                for wave in waves
                for item in wave.items
                if item.id == core_item.id
            ]

            self.assertEqual(len(dispatched), 1)
            self.assertEqual(
                dispatched[0].inputs["latest_diagnostic"]["failing_test"],
                "test_engine_turns",
            )
            self.assertIn("scope_artifacts", dispatched[0].inputs)

    def test_health_classifies_quality_failure_before_tool_approval_text(self):
        text = (
            "Python import validation failed for frontier_colony/domain/resources.py: "
            "ImportError: cannot import name normalize_mapping. "
            "Verification by run_code was denied because it requires approval."
        )

        self.assertEqual(HealthMonitor._classify_failure(text), "item_quality")

    def test_gate_pass_resets_stale_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile("Implement pkg/__init__.py tests/test_pkg.py")
            run_id = store.create_run("demo", tmpdir, contract=contract)
            gate_id = store.list_gates(run_id)[0].gate_id

            store.update_gate_status(run_id, gate_id, "FAILED", evidence=["Unit test validation failed: old failure"])
            store.update_gate_status(run_id, gate_id, "RUNNING")
            store.update_gate_status(run_id, gate_id, "PASSED", evidence=["Scope gate passed."])
            gate = store.get_gate(run_id, gate_id)

            self.assertEqual(gate.status, "PASSED")
            self.assertEqual(gate.evidence, ["Scope gate passed."])

    def test_large_project_flat_files_become_functional_teams_not_file_teams(self):
        files = [
            "starforge/__init__.py",
            "starforge/models.py",
            "starforge/resources.py",
            "starforge/state.py",
            "starforge/economy.py",
            "starforge/rules.py",
            "starforge/ai.py",
            "starforge/save_load.py",
            "starforge/scenarios.py",
            "starforge/cli.py",
            "tests/test_core.py",
            "tests/test_io.py",
            "tests/test_integration.py",
        ]

        contract = ContractCompiler().compile("Build a large Python package project with " + " ".join(files))
        scope_ids = {scope.id for scope in contract.work_scopes}
        item_ids = set(contract.item_by_id())

        self.assertFalse(any(scope_id.endswith(".py") for scope_id in scope_ids))
        self.assertTrue({"package", "domain", "core", "ai", "io", "interface"}.issubset(scope_ids))
        self.assertNotIn("tests", scope_ids)
        domain_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == "domain" and item.id.startswith("implement:domain")
            for artifact in item.target_artifacts
        ]
        core_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == "core" and item.id.startswith("implement:core")
            for artifact in item.target_artifacts
        ]
        self.assertIn("starforge/models.py", domain_artifacts)
        self.assertIn("starforge/economy.py", core_artifacts)
        io_artifacts = [
            artifact
            for item in contract.work_items
            if item.scope_id == "io" and item.id.startswith("implement:io")
            for artifact in item.target_artifacts
        ]
        self.assertIn("starforge/save_load.py", io_artifacts)
        self.assertIn("tests/test_core.py", {artifact for gate in contract.team_gates for artifact in gate.test_artifacts})
        self.assertFalse(any(item_id.startswith("test:") for item_id in item_ids))
        self.assertFalse(any(item_id.startswith("coding:starforge/") for item_id in item_ids))

    def test_max_steps_keeps_outer_team_parallelism(self):
        files = [
            "pkg/models/user.py",
            "pkg/models/resource.py",
            "pkg/core/state.py",
            "pkg/core/engine.py",
            "pkg/systems/economy.py",
            "pkg/systems/building.py",
            "pkg/ai/planner.py",
            "pkg/io/save_load.py",
            "pkg/io/scenarios.py",
            "pkg/cli/main.py",
            "tests/test_core.py",
            "tests/test_io.py",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            result = engine.run_auto("Build a large Python package project with " + " ".join(files), max_steps=4)
            statuses = {item.id: item.status for item in store.list_work_items(result.run_id)}
            completed_scaffolds = [
                item_id
                for item_id, status in statuses.items()
                if item_id.startswith("scaffold:") and status == "VERIFIED"
            ]

            self.assertEqual(result.status, "PAUSED")
            self.assertEqual(len(completed_scaffolds), 4)
            self.assertTrue(all(status in {"PENDING", "READY"} for item_id, status in statuses.items() if item_id.startswith("implement:")))

    def test_team_planner_materializes_scope_teams_from_large_contract(self):
        files = [
            "pkg/models/user.py",
            "pkg/models/resource.py",
            "pkg/core/state.py",
            "pkg/core/engine.py",
            "pkg/systems/economy.py",
            "pkg/systems/building.py",
            "pkg/ai/planner.py",
            "pkg/io/save_load.py",
            "pkg/io/scenarios.py",
            "pkg/cli/main.py",
            "tests/test_core.py",
            "tests/test_io.py",
        ]
        contract = ContractCompiler().compile("Build a large Python package project with " + " ".join(files))

        specs = TeamPlanner().materialize(contract)
        by_scope = {spec.scope_id: spec for spec in specs}

        self.assertGreaterEqual(len(specs), 5)
        self.assertIn("Team Lead", by_scope["core"].roles)
        self.assertIn("Implementation Worker", by_scope["domain"].roles)
        self.assertIn("Test Worker", by_scope["core"].roles)
        self.assertEqual(by_scope["domain"].workspace_plane, "worktree")
        self.assertIn("tests/test_core.py", by_scope["core"].owned_artifacts)
        self.assertNotIn("integration", by_scope)

    def test_run_engine_materializes_scope_teams_and_graph_reports_them(self):
        files = [
            "pkg/models/user.py",
            "pkg/core/engine.py",
            "pkg/systems/economy.py",
            "pkg/ai/planner.py",
            "pkg/io/save_load.py",
            "pkg/cli/main.py",
            "tests/test_core.py",
            "tests/test_io.py",
            "tests/test_integration.py",
            "pkg/core/rules.py",
            "pkg/models/resource.py",
            "pkg/systems/research.py",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            contract = ContractCompiler().compile("Build a large Python package project with " + " ".join(files))

            run_id = engine.start("demo", contract=contract)
            graph = engine.graph(run_id)
            teams = engine.teams(run_id)

            self.assertTrue(any(team["scope_id"] == "domain" for team in graph["teams"]))
            self.assertFalse(any(team["scope_id"] == "integration" for team in teams))
            self.assertTrue(any(gate["gate_id"] == "final" for gate in graph["gates"]))
            self.assertTrue(all("roles" in team for team in teams))

    def test_team_lead_brief_is_recorded_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)

            def executor(item, agent_name, state):
                workspace = get_current_workspace(tmpdir)
                with open(os.path.join(workspace, "alpha.py"), "w", encoding="utf-8") as handle:
                    handle.write("VALUE = 1\n")
                return {"output": "created alpha.py"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            run_id = engine.start("Implement alpha.py")
            engine.resume(run_id, max_steps=1)
            team = store.get_scope_team_run(run_id, "app")
            events = [event.event_type for event in store.list_events(run_id, limit=50)]

            self.assertIsNotNone(team)
            self.assertTrue(team.metadata.get("lead_briefed"))
            self.assertIn("Team Lead brief", team.metadata.get("lead_brief", ""))
            self.assertIn("team_lead_briefed", events)

    def test_task_harness_uses_existing_team_workspace_without_immediate_promotion(self):
        class CurrentWorkspaceAgent:
            def _execute_agent(self, state, next_available_agents, context_manager):
                workspace = get_current_workspace(os.getcwd())
                with open(os.path.join(workspace, "app.py"), "w", encoding="utf-8") as handle:
                    handle.write("def run():\n    return 'ok'\n")
                return GeneralState(
                    task=state.task,
                    sub_task=state.sub_task,
                    role="Backend_Engineer",
                    thinking="",
                    output="ok",
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            team_dir = os.path.join(tmpdir, "team-workspace")
            os.makedirs(team_dir, exist_ok=True)
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            harness = TaskHarness(config)
            state = GeneralState(
                task="demo",
                sub_task=(
                    "Module team: app\n"
                    "Execution plane: sandbox\n"
                    "Target files in this module wave:\n"
                    "- app.py\n"
                ),
                role="user",
                thinking="",
                output="",
            )

            with workspace_scope(team_dir):
                result = harness.execute(CurrentWorkspaceAgent(), "Backend_Engineer", state, [], None)

            self.assertEqual(result.validation_errors, [])
            self.assertTrue(os.path.exists(os.path.join(team_dir, "app.py")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "app.py")))

    def test_team_workspace_resyncs_late_system_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {
                            "id": "core",
                            "type": "code_module",
                            "team_policy": {"team_kind": "coding", "workspace_plane": "sandbox"},
                            "promotion_policy": {"mode": "after_team_gate"},
                        }
                    ],
                    "work_items": [
                        {
                            "id": "implement:core",
                            "kind": "coding",
                            "scope_id": "core",
                            "target_artifacts": ["pkg/core.py"],
                            "acceptance_criteria": ["core exists"],
                        }
                    ],
                    "team_gates": [{"scope_id": "core"}],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "core")
            self.assertFalse(os.path.exists(os.path.join(workspace, ".contractcoding", "interfaces", "core.json")))

            os.makedirs(os.path.join(tmpdir, ".contractcoding", "interfaces"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, ".contractcoding", "interface_tests"), exist_ok=True)
            with open(os.path.join(tmpdir, ".contractcoding", "interfaces", "core.json"), "w", encoding="utf-8") as handle:
                json.dump({"scope": "core"}, handle)
            with open(os.path.join(tmpdir, ".contractcoding", "interface_tests", "core.json"), "w", encoding="utf-8") as handle:
                json.dump({"tests": []}, handle)

            runtime.ensure_workspace(run, contract, "core")

            self.assertTrue(os.path.exists(os.path.join(workspace, ".contractcoding", "interfaces", "core.json")))
            self.assertTrue(os.path.exists(os.path.join(workspace, ".contractcoding", "interface_tests", "core.json")))

    def test_team_workspace_refreshes_promoted_dependency_artifacts_without_overwriting_owned_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {"id": "package", "type": "code_module", "artifacts": ["pkg/__init__.py"]},
                        {
                            "id": "interface",
                            "type": "code_module",
                            "artifacts": ["pkg/interface/cli.py"],
                            "team_policy": {"team_kind": "coding", "workspace_plane": "sandbox"},
                        },
                    ],
                    "work_items": [
                        {
                            "id": "implement:interface",
                            "kind": "coding",
                            "scope_id": "interface",
                            "target_artifacts": ["pkg/interface/cli.py"],
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "team_gates": [{"scope_id": "interface"}],
                    "final_gate": {"package_roots": ["pkg"]},
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "interface")
            os.makedirs(os.path.join(workspace, "pkg", "interface"), exist_ok=True)
            with open(os.path.join(workspace, "pkg", "interface", "cli.py"), "w", encoding="utf-8") as handle:
                handle.write("OWNED = 'team'\n")

            os.makedirs(os.path.join(tmpdir, "pkg", "io"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("PACKAGE = 'promoted'\n")
            with open(os.path.join(tmpdir, "pkg", "io", "save_load.py"), "w", encoding="utf-8") as handle:
                handle.write("IO = 'promoted'\n")
            os.makedirs(os.path.join(tmpdir, "pkg", "interface"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "interface", "cli.py"), "w", encoding="utf-8") as handle:
                handle.write("OWNED = 'main'\n")

            runtime.ensure_workspace(run, contract, "interface")

            self.assertTrue(os.path.exists(os.path.join(workspace, "pkg", "__init__.py")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "pkg", "io", "save_load.py")))
            with open(os.path.join(workspace, "pkg", "interface", "cli.py"), encoding="utf-8") as handle:
                self.assertIn("OWNED = 'team'", handle.read())

    def test_team_runtime_promotes_after_scope_gate_verification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "pkg", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "coding:pkg/__init__.py",
                            "kind": "coding",
                            "title": "Package",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/__init__.py"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "team_gates": [{"scope_id": "pkg"}],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_gate_status(run_id, "team:pkg", "PASSED")
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "pkg")
            os.makedirs(os.path.join(workspace, "pkg"), exist_ok=True)
            with open(os.path.join(workspace, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            promoted = runtime.promote_if_ready(run, contract, "pkg")

            self.assertTrue(promoted)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "pkg", "__init__.py")))
            self.assertEqual(store.get_scope_team_run(run_id, "pkg").status, "CLOSED")
            metadata_path = os.path.join(tmpdir, ".contractcoding", "promotions", run_id, "pkg.json")
            self.assertTrue(os.path.exists(metadata_path))
            with open(metadata_path, encoding="utf-8") as handle:
                metadata = json.load(handle)
            self.assertEqual(metadata["scope_id"], "pkg")
            self.assertEqual(metadata["owned_files"], ["pkg/__init__.py"])
            self.assertEqual(metadata["unowned_files"], [])
            self.assertIn("pkg/__init__.py", metadata["patch_stats"])

    def test_team_runtime_partially_promotes_verified_artifacts_before_team_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "pkg", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "implement:pkg",
                            "kind": "coding",
                            "title": "Package",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/__init__.py"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "team_gates": [
                        {
                            "scope_id": "pkg",
                            "deterministic_checks": ["artifact_coverage", "syntax_import", "functional_smoke"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "pkg")
            os.makedirs(os.path.join(workspace, "pkg"), exist_ok=True)
            with open(os.path.join(workspace, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            promoted = runtime.promote_verified_artifacts(run, contract, "pkg")

            self.assertEqual(promoted, {"pkg/__init__.py"})
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "pkg", "__init__.py")))
            team = store.get_scope_team_run(run_id, "pkg")
            self.assertNotEqual(team.status, "CLOSED")
            self.assertEqual(team.metadata["partial_promoted_files"], ["pkg/__init__.py"])
            self.assertEqual(store.get_gate(run_id, "team:pkg").status, "PENDING")

    def test_team_runtime_promotes_owned_file_created_after_plane_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {
                            "id": "package",
                            "type": "code_module",
                            "artifacts": ["pkg/__init__.py"],
                            "team_policy": {"team_kind": "coding", "workspace_plane": "sandbox"},
                        }
                    ],
                    "work_items": [
                        {
                            "id": "implement:package",
                            "kind": "coding",
                            "scope_id": "package",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/__init__.py"],
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "team_gates": [{"scope_id": "package"}],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_gate_status(run_id, "team:package", "PASSED")
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "package")

            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 'main-stale'\n")
            os.makedirs(os.path.join(workspace, "pkg"), exist_ok=True)
            with open(os.path.join(workspace, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 'team-final'\n")

            promoted = runtime.promote_if_ready(run, contract, "package")

            self.assertTrue(promoted)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), encoding="utf-8") as handle:
                self.assertIn("team-final", handle.read())
            self.assertTrue(
                any(event.event_type == "late_owned_baselines_refreshed" for event in store.list_events(run_id))
            )

    def test_team_runtime_owner_wins_for_owned_artifact_promotion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "planner.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 'base'\n")
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {
                            "id": "ai",
                            "type": "code_module",
                            "artifacts": ["pkg/planner.py"],
                            "team_policy": {"team_kind": "coding", "workspace_plane": "sandbox"},
                        }
                    ],
                    "work_items": [
                        {
                            "id": "implement:ai",
                            "kind": "coding",
                            "scope_id": "ai",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/planner.py"],
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "team_gates": [{"scope_id": "ai"}],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_gate_status(run_id, "team:ai", "PASSED")
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "ai")
            with open(os.path.join(tmpdir, "pkg", "planner.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 'main-later'\n")
            with open(os.path.join(workspace, "pkg", "planner.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 'team-repair'\n")

            promoted = runtime.promote_if_ready(run, contract, "ai")

            self.assertTrue(promoted)
            with open(os.path.join(tmpdir, "pkg", "planner.py"), encoding="utf-8") as handle:
                self.assertIn("team-repair", handle.read())
            self.assertTrue(any(event.event_type == "owned_artifacts_promoted" for event in store.list_events(run_id)))

    def test_promotion_metadata_classifies_owned_unowned_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            working = os.path.join(tmpdir, "team")
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(working, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "owned.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(working, "pkg", "owned.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 2\n")
            with open(os.path.join(working, "pkg", "extra.py"), "w", encoding="utf-8") as handle:
                handle.write("EXTRA = True\n")

            writer = PromotionMetadataWriter(tmpdir)
            summary = writer.build(
                run_id="run-1",
                team_id="team-1",
                scope_id="pkg",
                working_dir=working,
                base_dir=tmpdir,
                changed_files=["pkg/owned.py", "pkg/extra.py"],
                owned_artifacts=["pkg/owned.py"],
                promoted_files=["pkg/owned.py"],
                conflict_reason="demo conflict",
            )
            rel_path = writer.write(summary)

            self.assertEqual(summary.owned_files, ["pkg/owned.py"])
            self.assertEqual(summary.unowned_files, ["pkg/extra.py"])
            self.assertEqual(summary.conflict_reason, "demo conflict")
            self.assertTrue(os.path.exists(os.path.join(tmpdir, rel_path)))

    def test_team_runtime_promotes_hidden_contractcoding_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "write memo",
                {
                    "goals": ["write memo"],
                    "work_scopes": [
                        {
                            "id": "writing",
                            "type": "doc",
                            "artifacts": [".contractcoding/output.md", ".contractcoding/scope_reports/writing.json"],
                            "team_policy": {"team_kind": "doc", "workspace_plane": "sandbox"},
                            "promotion_policy": {"mode": "artifact"},
                        }
                    ],
                    "work_items": [
                        {
                            "id": "doc:main",
                            "kind": "doc",
                            "title": "Memo",
                            "owner_profile": "Technical_Writer",
                            "scope_id": "writing",
                            "status": "VERIFIED",
                            "target_artifacts": [".contractcoding/output.md"],
                            "acceptance_criteria": ["memo exists"],
                        },
                    ],
                    "team_gates": [{"scope_id": "writing"}],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("write memo", tmpdir, contract=contract)
            store.update_gate_status(run_id, "team:writing", "PASSED")
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "writing")
            os.makedirs(os.path.join(workspace, ".contractcoding", "scope_reports"), exist_ok=True)
            with open(os.path.join(workspace, ".contractcoding", "output.md"), "w", encoding="utf-8") as handle:
                handle.write("# Memo\n\nDone.\n")
            with open(
                os.path.join(workspace, ".contractcoding", "scope_reports", "writing.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write('{"ok": true}\n')

            promoted = runtime.promote_if_ready(run, contract, "writing")

            self.assertTrue(promoted)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "output.md")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "scope_reports", "writing.json")))

    def test_team_runtime_does_not_promote_synced_system_contract_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), EXECUTION_PLANE="sandbox")
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {
                            "id": "ai",
                            "type": "code_module",
                            "artifacts": [
                                "pkg/ai.py",
                                ".contractcoding/interfaces/ai.json",
                                ".contractcoding/interface_tests/ai.json",
                            ],
                            "team_policy": {"team_kind": "coding", "workspace_plane": "sandbox"},
                        }
                    ],
                    "work_items": [
                        {
                            "id": "implement:ai",
                            "kind": "coding",
                            "scope_id": "ai",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/ai.py"],
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "team_gates": [{"scope_id": "ai"}],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_gate_status(run_id, "team:ai", "PASSED")
            run = store.get_run(run_id)
            runtime = TeamRuntime(config=config, store=store)
            runtime.ensure_teams(run_id, contract)
            workspace = runtime.ensure_workspace(run, contract, "ai")
            os.makedirs(os.path.join(workspace, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(workspace, ".contractcoding", "interfaces"), exist_ok=True)
            os.makedirs(os.path.join(workspace, ".contractcoding", "interface_tests"), exist_ok=True)
            with open(os.path.join(workspace, "pkg", "ai.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(workspace, ".contractcoding", "interfaces", "ai.json"), "w", encoding="utf-8") as handle:
                handle.write('{"scope": "ai"}\n')
            with open(
                os.path.join(workspace, ".contractcoding", "interface_tests", "ai.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write('{"tests": []}\n')

            promoted = runtime.promote_if_ready(run, contract, "ai")

            self.assertTrue(promoted)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "pkg", "ai.py")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, ".contractcoding", "interfaces", "ai.json")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, ".contractcoding", "interface_tests", "ai.json")))

    def test_dependency_impact_marks_only_dependent_team_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {"id": "core", "type": "code_module"},
                        {"id": "ui", "type": "code_module"},
                        {"id": "docs", "type": "doc"},
                    ],
                    "work_items": [
                        {
                            "id": "core:a",
                            "kind": "coding",
                            "title": "Core",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "core",
                            "target_artifacts": ["core/a.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "ui:b",
                            "kind": "coding",
                            "title": "UI",
                            "owner_profile": "Frontend_Engineer",
                            "scope_id": "ui",
                            "depends_on": ["core:a"],
                            "target_artifacts": ["ui/b.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "docs:c",
                            "kind": "doc",
                            "title": "Docs",
                            "owner_profile": "Technical_Writer",
                            "scope_id": "docs",
                            "target_artifacts": ["README.md"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run("demo", tmpdir, contract=contract)
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            TeamRuntime(config, store).ensure_teams(run_id, contract)
            store.update_gate_status(run_id, "team:ui", "PASSED", evidence=["ui gate passed"])

            stale = DependencyImpactAnalyzer(store).mark_stale(run_id, "core", "public API changed")

            self.assertEqual(stale, [store.get_scope_team_run(run_id, "ui").id])
            self.assertEqual(store.get_scope_team_run(run_id, "ui").status, "STALE_DEPENDENCY")
            self.assertNotEqual(store.get_scope_team_run(run_id, "docs").status, "STALE_DEPENDENCY")

    def test_contract_json_is_plan_only_and_markdown_projection_has_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile("Create a small Python CLI utility")
            store = ContractFileStore(tmpdir)
            store.write(contract)

            with open(store.json_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["version"], "8")
            self.assertIn("requirements", payload)
            self.assertIn("architecture", payload)
            self.assertIn("milestones", payload)
            self.assertIn("phase_plan", payload)
            self.assertIn("interfaces", payload)
            self.assertIn("teams", payload)
            self.assertIn("work", payload)
            self.assertIn("gates", payload)
            self.assertIn("kernel", payload)
            self.assertNotIn("intent", payload)
            first_item = payload["work"][0]

            self.assertNotIn("status", first_item)
            self.assertNotIn("evidence", first_item)
            self.assertTrue(store.projection_in_sync(contract))
            with open(store.markdown_path, "r", encoding="utf-8") as handle:
                self.assertIn(contract.content_hash(), handle.read())

    def test_v8_contract_projection_is_milestone_orchestrated_and_kernel_roundtrips(self):
        contract = ContractCompiler().compile(
            "Build a large Python package with "
            "pkg/models/state.py pkg/core/engine.py pkg/interface/repl.py "
            "pkg/io/save_load.py tests/test_core.py"
        )
        payload = json.loads(contract.to_json())

        self.assertEqual(
            set(["requirements", "architecture", "milestones", "phase_plan", "teams", "interfaces", "work", "gates", "policy", "kernel"]).issubset(payload),
            True,
        )
        self.assertNotIn("work_items", payload)
        self.assertIn("work_items", payload["kernel"])
        self.assertNotIn("interfaces", payload["kernel"])
        self.assertTrue(payload["requirements"]["status"], "FROZEN")
        self.assertTrue(any(interface["status"] == "FROZEN" for interface in payload["interfaces"]))
        self.assertTrue(any(interface.get("scaffold") for interface in payload["interfaces"]))
        self.assertTrue(any(interface.get("conformance_tests") for interface in payload["interfaces"]))
        self.assertTrue(any(team["id"] == "interface" for team in payload["teams"]))
        self.assertFalse(any("promotion_policy" in team for team in payload["teams"]))

        roundtripped = load_contract_json(contract.to_json())

        self.assertEqual(len(roundtripped.work_items), len(contract.work_items))
        self.assertTrue(roundtripped.team_gates)

    def test_progressive_freezing_blocks_build_until_team_interface_frozen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="root", type="root"), WorkScope(id="core", type="code_module")],
                work_items=[
                    WorkItem(
                        id="implement:core",
                        kind="coding",
                        title="Implement core",
                        owner_profile="Backend_Engineer",
                        scope_id="core",
                        status="READY",
                        target_artifacts=["pkg/core/engine.py"],
                        acceptance_criteria=["done"],
                    )
                ],
                milestones=[MilestoneSpec(id="team.interfaces"), MilestoneSpec(id="team.build", depends_on=["team.interfaces"], mode="parallel")],
                interfaces=[
                    InterfaceSpec(
                        id="core.team",
                        owner_team="core",
                        artifact="pkg/core/engine.py",
                        status="DRAFT",
                        symbols=[{"kind": "class", "name": "SimulationEngine"}],
                    )
                ],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)

            scheduler = Scheduler(store)

            self.assertEqual(scheduler.next_wave(run_id), [])
            self.assertIn("Waiting on team-local interface freeze", scheduler.blocked_reasons(run_id)[0].reason)

    def test_team_gate_checks_frozen_interface_before_behavior_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "core"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "core", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "core", "engine.py"), "w", encoding="utf-8") as handle:
                handle.write("class SomethingElse:\n    pass\n")
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="root", type="root"), WorkScope(id="core", type="code_module")],
                work_items=[
                    WorkItem(
                        id="implement:core",
                        kind="coding",
                        scope_id="core",
                        status="VERIFIED",
                        target_artifacts=["pkg/core/engine.py"],
                        acceptance_criteria=["done"],
                    )
                ],
                team_gates=[TeamGateSpec(scope_id="core")],
                interfaces=[
                    InterfaceSpec(
                        id="core.critical",
                        owner_team="core",
                        artifact="pkg/core/engine.py",
                        status="FROZEN",
                        critical=True,
                        symbols=[{"kind": "class", "name": "SimulationEngine", "methods": ["run_turn(state) -> TurnReport"]}],
                    )
                ],
            )

            result = GateChecker(tmpdir).check_team_gate(
                contract=contract,
                scope=contract.scope_by_id()["core"],
                gate=contract.team_gates[0],
                scope_items=contract.work_items,
            )

            self.assertFalse(result.ok)
            self.assertTrue(any("missing class `SimulationEngine`" in error for error in result.errors))

    def test_contract_json_omits_derived_gate_and_default_fields_but_roundtrips(self):
        contract = ContractCompiler().compile(
            "Build a large Python package project with "
            "pkg/models/user.py pkg/core/engine.py pkg/io/save_load.py "
            "pkg/cli/main.py tests/test_core.py tests/test_io.py"
        )
        payload = contract.to_record()
        scope = next(scope for scope in payload["work_scopes"] if scope["id"] == "core")
        gate = next(gate for gate in payload["team_gates"] if gate["scope_id"] == "core")
        final_gate = payload["final_gate"]

        self.assertNotIn("artifacts", scope)
        self.assertNotIn("conflict_keys", scope)
        self.assertNotIn("execution_plane_policy", scope)
        self.assertNotIn("status", gate)
        self.assertNotIn("evidence", gate)
        self.assertIn("test_plan", gate)
        self.assertNotIn("inputs", final_gate)
        self.assertLess(len(contract.to_json()), 40000)

        roundtripped = ContractCompiler().compile(contract.goals[0], load_contract_json(contract.to_json()))
        materialized_gate = next(gate for gate in roundtripped.team_gates if gate.scope_id == "core")
        materialized_final = roundtripped.final_gate

        self.assertIn("pkg/core/engine.py", materialized_gate.test_plan["required_public_interfaces"])
        self.assertIn("tests/test_core.py", materialized_gate.test_artifacts)
        self.assertTrue(materialized_final.required_artifacts)

    def test_agent_input_packet_slices_contract_to_direct_dependencies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile("Create a medium Tic Tac Toe command-line game with AI")
            manager = ContextManager(config, agents=["Backend_Engineer"])
            item = contract.item_by_id()["coding:main.py"]
            packet = manager.build_agent_input_packet(
                task="demo",
                contract=contract,
                item=item,
                scope=contract.scope_by_id()[item.scope_id],
                wave_kind="implementation",
                runtime_items=contract.work_items,
            )
            rendered = packet.render(12000)

            self.assertIn("coding:game_engine.py", rendered)
            self.assertIn("coding:ai_player.py", rendered)
            self.assertNotIn("coding:test_game_engine.py", rendered)
            self.assertIn("allowed_artifacts: main.py", rendered)
            self.assertEqual(packet.packet_version, "5")
            self.assertIn("verification_layer: self_check", rendered)
            self.assertTrue(packet.scope_test_hints)
            self.assertIn("coding.implementation", packet.selected_skills)
            self.assertIn("read_lines", packet.tool_policy["allowed_tools"])
            self.assertIn("worker_protocol_version: 2", rendered)

    def test_plan_critic_rejects_interface_vertical_slice_and_missing_gate_dependencies(self):
        contract = ContractSpec(
            goals=["demo"],
            work_scopes=[
                WorkScope(id="core", type="code_module", artifacts=["pkg/core.py"]),
                WorkScope(id="interface", type="code_module", artifacts=["pkg/cli.py"]),
            ],
            work_items=[
                WorkItem(
                    id="implement:core",
                    kind="coding",
                    scope_id="core",
                    target_artifacts=["pkg/core.py"],
                    acceptance_criteria=["core works"],
                    inputs={"phase_id": "feature:core"},
                ),
                WorkItem(
                    id="implement:interface",
                    kind="coding",
                    scope_id="interface",
                    target_artifacts=["pkg/cli.py"],
                    acceptance_criteria=["cli works"],
                    depends_on=["implement:core"],
                    inputs={"phase_id": "vertical_slice"},
                ),
            ],
            phase_plan=[
                {"phase_id": "vertical_slice", "goal": "slice"},
                {"phase_id": "feature:core", "goal": "core"},
            ],
            team_gates=[
                TeamGateSpec(scope_id="core"),
                TeamGateSpec(scope_id="interface", test_plan={"test_strata": "scope_local"}),
            ],
            final_gate=FinalGateSpec(required_artifacts=["pkg/core.py", "pkg/cli.py"]),
            acceptance_criteria=["done"],
        )

        result = PlanCritic().review_contract(contract)

        self.assertFalse(result.accepted)
        rendered = " ".join(result.errors)
        self.assertIn("vertical_slice", rendered)
        self.assertIn("dependency_scope_ids", rendered)

    def test_plan_critic_rejects_false_done_ops_contract_for_package_task(self):
        task = "Build a dependency-free Python package named chronicle_forge with CLI and unittest tests."
        contract = ContractSpec(
            goals=[task],
            requirements={"summary": task, "delivery_type": "coding"},
            work_scopes=[WorkScope(id="ops", type="ops", artifacts=[".contractcoding/ops_plan.md"])],
            work_items=[
                WorkItem(
                    id="ops:main",
                    kind="ops",
                    scope_id="ops",
                    target_artifacts=[".contractcoding/ops_plan.md"],
                    acceptance_criteria=["plan exists"],
                )
            ],
            team_gates=[TeamGateSpec(scope_id="ops")],
            final_gate=FinalGateSpec(required_artifacts=[".contractcoding/ops_plan.md"]),
            metadata={"delivery_type": "ops"},
            acceptance_criteria=["done"],
        )

        result = PlanCritic().review_contract(contract)

        self.assertFalse(result.accepted)
        rendered = " ".join(result.errors)
        self.assertIn("no coding work items", rendered)
        self.assertIn("delivery_type is ops", rendered)

    def test_agent_input_packet_selects_repair_skills_and_locks_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile("Implement pkg/core.py tests/test_core.py")
            manager = ContextManager(config, agents=["Backend_Engineer"])
            item = next(
                work_item
                for work_item in contract.work_items
                if work_item.kind == "coding" and work_item.target_artifacts == ["pkg/core.py"]
            )
            payload = item.to_record()
            payload["inputs"] = {
                **payload.get("inputs", {}),
                "repair_ticket_id": "ticket-1",
                "diagnostics": [
                    {
                        "gate_id": "final",
                        "scope_id": "integration",
                        "failure_kind": "unittest_assertion",
                        "failing_test": "tests.test_core.test_turn",
                        "test_artifacts": ["tests/test_core.py"],
                        "suspected_implementation_artifacts": ["pkg/core.py"],
                        "repair_instruction": "Fix turn behavior without editing tests.",
                    }
                ],
            }
            repair_item = WorkItem.from_mapping(payload)

            packet = manager.build_agent_input_packet(
                task="demo",
                contract=contract,
                item=repair_item,
                scope=contract.scope_by_id()[repair_item.scope_id],
                wave_kind="implementation",
                runtime_items=contract.work_items,
            )

            self.assertIn("coding.repair", packet.selected_skills)
            self.assertIn("coding.integration_repair", packet.selected_skills)
            self.assertIn("read_lines", packet.tool_policy["allowed_tools"])
            self.assertNotIn("write_file", packet.tool_policy["allowed_tools"])
            self.assertEqual(packet.repair_packet["repair_ticket_id"], "ticket-1")
            self.assertIn("tests/test_core.py", packet.locked_artifacts)
            self.assertIn("locked_artifacts_are_read_only", packet.worker_protocol)

    def test_agent_memory_is_isolated_by_run_and_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = ContextManager(config, agents=["Backend_Engineer"], memory_window=5)
            domain_key = manager.memory_key("Backend_Engineer", run_id="run-a", scope_id="domain")
            core_key = manager.memory_key("Backend_Engineer", run_id="run-a", scope_id="core")
            manager.add_message(
                domain_key,
                GeneralState(task="demo", sub_task="", role="assistant", thinking="", output="domain-only decision"),
            )
            manager.add_message(
                core_key,
                GeneralState(task="demo", sub_task="", role="assistant", thinking="", output="core-only decision"),
            )

            manager.set_active_memory_key("Backend_Engineer", domain_key)
            try:
                domain_history = manager.build_message_history("Backend_Engineer")
            finally:
                manager.clear_active_memory_key("Backend_Engineer")

            text = "\n".join(message["content"] for message in domain_history)
            self.assertIn("domain-only decision", text)
            self.assertNotIn("core-only decision", text)

    def test_run_shorthand_cli_normalizes_to_auto_without_difficulty(self):
        argv = normalize_argv(["run", "Create a terminal game", "--backend", "openai"])

        self.assertEqual(argv[:2], ["run", "Create a terminal game"])
        self.assertIn("--backend", argv)

    def test_cli_global_and_run_scoped_backend_and_max_steps_are_resolved(self):
        parser = build_parser()

        global_args = parser.parse_args(["--backend", "openai", "--max-steps", "0", "run", "demo"])
        run_args = parser.parse_args(["run", "demo", "--backend", "openai", "--max-steps", "3"])

        self.assertEqual(build_config(global_args).LLM_BACKEND, "openai")
        self.assertEqual(global_args.max_steps, 0)
        self.assertIsNone(global_args.run_max_steps)
        self.assertEqual(build_config(run_args).LLM_BACKEND, "openai")
        self.assertEqual(run_args.run_max_steps, 3)

    def test_openai_backend_uses_azure_client_when_api_version_is_configured(self):
        with mock.patch("ContractCoding.llm.openai_backend.AzureOpenAI") as azure_client:
            backend = OpenAIBackend(
                api_key="secret",
                api_base="https://example.openai.azure.com/openai/deployments/demo/chat/completions",
                deployment_name="gpt-5.4-2026-03-05",
                api_version="2026-03-01",
            )

        azure_client.assert_called_once_with(
            azure_endpoint="https://example.openai.azure.com",
            api_key="secret",
            api_version="2026-03-01",
        )
        self.assertEqual(backend.model, "gpt-5.4-2026-03-05")
        self.assertEqual(normalize_azure_endpoint("https://example.openai.azure.com/openai/deployments/demo/chat/completions"), "https://example.openai.azure.com")

    def test_openai_backend_adapts_chat_params_for_new_model_families(self):
        backend = OpenAIBackend(
            api_key="secret",
            api_base="https://api.openai.com/v1",
            deployment_name="gpt-5.4-2026-03-05",
        )

        adapted = backend._adapt_chat_params_after_error(
            {"max_tokens": 100, "temperature": 0.2},
            Exception("max_tokens is not supported with this model; temperature is unsupported"),
        )

        self.assertEqual(adapted["max_completion_tokens"], 100)
        self.assertNotIn("max_tokens", adapted)
        self.assertNotIn("temperature", adapted)
        self.assertEqual(backend.request_timeout, 120)
        self.assertEqual(backend.tool_timeout, 300)

        custom = OpenAIBackend(
            api_key="secret",
            api_base="https://api.openai.com/v1",
            deployment_name="gpt-5.4-2026-03-05",
            request_timeout=180,
            tool_timeout=600,
            image_timeout=240,
            max_tool_iterations=12,
        )
        self.assertEqual(custom.request_timeout, 180)
        self.assertEqual(custom.tool_timeout, 600)
        self.assertEqual(custom.image_timeout, 240)
        self.assertEqual(custom.tool_loop_timeout, 900)
        self.assertEqual(custom.max_tool_iterations, 12)
        self.assertIn("missing target artifacts", custom._openai_tool_policy_message()["content"])

    def test_openai_backend_records_timeout_attempts_and_retries(self):
        def fake_response(content="<output>ok</output>"):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(role="assistant", content=content))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=mock.Mock(side_effect=[TimeoutError("request timed out"), fake_response()])
                )
            )
        )
        backend = OpenAIBackend(
            api_key="secret",
            api_base="https://api.openai.com/v1",
            deployment_name="gpt-5.4-2026-03-05",
            request_timeout=60,
        )
        backend.client = client

        with mock.patch("ContractCoding.llm.openai_backend.time.sleep"):
            response = backend.chat([{"role": "user", "content": "hello"}])
        observed = response_observability(response, backend)

        self.assertEqual(response.content, "<output>ok</output>")
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertEqual(observed["attempt_count"], 2)
        self.assertEqual(observed["timeout_count"], 1)
        self.assertEqual(observed["empty_response_count"], 0)
        self.assertEqual(response.raw["attempts"][0]["returncode"], "timeout")
        self.assertEqual(response.raw["attempts"][1]["content_preview"], "<output>ok</output>")

    def test_openai_backend_tool_loop_timeout_reports_infra_failure(self):
        def fake_response(tool_calls=None, content=""):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            role="assistant",
                            content=content,
                            tool_calls=tool_calls,
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

        tool_call = SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="read_lines", arguments=json.dumps({"file_path": "pkg/mod.py"})),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=mock.Mock(return_value=fake_response(tool_calls=[tool_call])))
            )
        )
        backend = OpenAIBackend(
            api_key="secret",
            api_base="https://api.openai.com/v1",
            deployment_name="gpt-5.4-2026-03-05",
            tool_loop_timeout=1,
            max_tool_iterations=5,
        )
        backend.client = client

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "mod.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            backend.workspace_dir = tmpdir
            backend.allowed_artifacts = ["pkg/mod.py"]
            with mock.patch("ContractCoding.llm.openai_backend.time.perf_counter", side_effect=[0, 0, 2, 2, 2, 2]):
                response = backend.chat_with_tools(
                    [{"role": "user", "content": "inspect"}],
                    build_file_tools(tmpdir),
                )

        observed = response_observability(response, backend)
        self.assertTrue(observed["infra_failure"])
        self.assertEqual(observed["failure_kind"], "timeout")
        self.assertEqual(observed["timeout_count"], 1)
        self.assertEqual(observed["empty_response_count"], 0)

    def test_openai_backend_submit_result_is_terminal_stop(self):
        def fake_response(tool_calls=None, content=""):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            role="assistant",
                            content=content,
                            tool_calls=tool_calls,
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

        tool_call = SimpleNamespace(
            id="call_done",
            type="function",
            function=SimpleNamespace(
                name="submit_result",
                arguments=json.dumps(
                    {
                        "summary": "implemented CLI JSON output",
                        "changed_files": ["pkg/cli.py"],
                        "evidence": ["interface tests pass"],
                    }
                ),
            ),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=mock.Mock(return_value=fake_response(tool_calls=[tool_call]))))
        )
        backend = OpenAIBackend(
            api_key="secret",
            api_base="https://api.openai.com/v1",
            deployment_name="gpt-5.4-2026-03-05",
            max_tool_iterations=5,
        )
        backend.client = client

        response = backend.chat_with_tools([{"role": "user", "content": "finish"}], build_file_tools("."))
        observed = response_observability(response, backend)

        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(response.raw["stop_reason"], "submit_result")
        self.assertEqual(observed["terminal_tool"], "submit_result")
        self.assertIn("implemented CLI JSON output", response.content)
        self.assertEqual(response.raw["terminal_result"]["changed_files"], ["pkg/cli.py"])

    def test_report_blocker_terminal_payload_is_treated_as_blocker(self):
        payload = {
            "output": "blocked",
            "agent_terminal": {
                "tool_name": "report_blocker",
                "blocker_type": "out_of_scope_repair",
                "required_artifacts": ["pkg/core/runtime.py"],
                "current_allowed_artifacts": ["pkg/interface/cli.py"],
                "reason": "runtime owner must fix completion semantics",
            },
        }

        blocker = TeamExecutor._payload_agent_blocker(payload)

        self.assertIn("pkg/core/runtime.py", blocker)
        self.assertIn("out_of_scope_repair", blocker)

    def test_report_blocker_for_allowed_artifact_is_invalid_blocker(self):
        payload = {
            "output": "blocked",
            "agent_terminal": {
                "tool_name": "report_blocker",
                "blocker_type": "out_of_scope_repair",
                "required_artifacts": ["pkg/interface/repl.py"],
                "current_allowed_artifacts": ["pkg/interface/repl.py"],
                "reason": "PatchGuard rolled back my edit",
            },
            "wave_allowed_artifacts": ["pkg/interface/repl.py"],
        }

        blocker = TeamExecutor._payload_agent_blocker(payload)

        self.assertIn("Invalid blocker", blocker)
        self.assertIn("already allowed", blocker)
        self.assertNotIn("Agent reported a blocker", blocker)

    def test_openai_backend_closes_tool_schemas_and_passes_allowed_tools_to_governor(self):
        tools = build_file_tools(".")
        schema = OpenAIBackend._strict_tool_schema(tools[0].openai_schema)

        self.assertTrue(schema["function"]["strict"])
        self.assertFalse(schema["function"]["parameters"]["additionalProperties"])

        governor = ToolGovernor(
            approval_mode="auto-edit",
            allowed_tools=["read_lines"],
            allowed_artifacts=["pkg/mod.py"],
        )
        self.assertTrue(governor.decide("read_lines", {"path": "pkg/mod.py", "start_line": 1, "end_line": 5}).allowed)
        self.assertFalse(governor.decide("write_file", {"path": "pkg/mod.py"}).allowed)

    def test_openai_dispatch_records_no_backend_probe_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), LLM_BACKEND="openai")
            store = RunStore.for_workspace(tmpdir)
            item = WorkItem(
                id="doc:summary",
                kind="doc",
                title="Summary",
                owner_profile="Technical_Writer",
                target_artifacts=["summary.md"],
                acceptance_criteria=["summary exists"],
            )

            def executor(work_item, agent_name, state):
                with open(os.path.join(tmpdir, "summary.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Summary\n\nThis is a substantive summary for the OpenAI-first runtime path.\n")
                return GeneralState(task=state.task, sub_task=state.sub_task, role=agent_name, output="done")

            engine = RunEngine(config=config, store=store, step_executor=executor)
            run_id = engine.start("demo", initial_work_items=[item])

            engine.resume(run_id, max_steps=1)

            run = store.get_run(run_id)
            self.assertNotIn("backend_probe", run.metadata)

    def test_team_executor_records_backend_neutral_llm_observability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile("Implement demo.py")
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            wave = Scheduler(store).next_wave(run_id)[0]

            def executor(item, agent_name, state):
                workspace = get_current_workspace(tmpdir)
                with open(os.path.join(workspace, "demo.py"), "w", encoding="utf-8") as handle:
                    handle.write("VALUE = 1\n")
                return GeneralState(
                    task=state.task,
                    sub_task=state.sub_task,
                    role=agent_name,
                    output="created demo.py",
                    task_requirements={
                        "llm_observability": {
                            "backend": "openai",
                            "prompt_tokens": 12,
                            "completion_tokens": 7,
                            "tool_intent_count": 1,
                            "tool_result_count": 1,
                        }
                    },
                )

            result = TeamExecutor(config=config, store=store, step_executor=executor).execute(run, wave)
            events = [event for event in store.list_events(run_id, limit=50) if event.event_type == "llm_observed"]
            report = RunEngine(config=config, store=store, step_executor=executor).report(run_id)

            self.assertTrue(result.ok)
            self.assertEqual(events[0].payload["backend"], "openai")
            self.assertEqual(events[0].payload["prompt_tokens"], 12)
            self.assertIn("LLM:", report)

    def test_team_executor_rejects_llm_infra_failure_even_when_files_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile("Implement demo.py")
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            wave = Scheduler(store).next_wave(run_id)[0]

            def executor(item, agent_name, state):
                workspace = get_current_workspace(tmpdir)
                with open(os.path.join(workspace, "demo.py"), "w", encoding="utf-8") as handle:
                    handle.write("VALUE = 1\n")
                return {
                    "output": "tool loop stopped before submit_result",
                    "llm_observability": {
                        "backend": "openai",
                        "infra_failure": True,
                        "failure_kind": "tool_loop_exhausted",
                        "returncode": "error",
                    },
                }

            result = TeamExecutor(config=config, store=store, step_executor=executor).execute(run, wave)

            self.assertFalse(result.ok)
            self.assertEqual(store.get_work_item(run_id, "coding:demo.py").status, "BLOCKED")
            latest = store.latest_steps(run_id, limit=1)[0]
            self.assertEqual(latest.status, "ERROR")
            self.assertIn("infrastructure failure", latest.error)

    @unittest.skipUnless(os.getenv("RUN_OPENAI_E2E") == "1", "set RUN_OPENAI_E2E=1 to run real OpenAI API smoke")
    def test_optional_real_openai_api_smoke(self):
        missing = [name for name in ("API_KEY", "BASE_URL", "API_VERSION") if not os.getenv(name)]
        if missing:
            self.skipTest("OpenAI smoke env vars are not all present: " + ", ".join(missing))
        backend = OpenAIBackend(
            api_key=os.getenv("API_KEY", ""),
            api_base=os.getenv("BASE_URL", ""),
            api_version=os.getenv("API_VERSION", ""),
            deployment_name=os.getenv("MODEL_NAME", os.getenv("OPENAI_DEPLOYMENT_NAME", "gpt-5.4-2026-03-05")),
            max_tokens=32,
            request_timeout=60,
        )

        response = backend.chat(
            [{"role": "user", "content": "Reply with exactly CONTRACTCODING_OPENAI_SMOKE_OK."}]
        )

        self.assertIn("CONTRACTCODING_OPENAI_SMOKE_OK", response.content)

    def test_openai_native_tool_calls_use_contractcoding_patch_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            target = os.path.join(tmpdir, "pkg", "mod.py")
            original = "def value():\n    return 1\n"
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(original)

            def fake_response(content="", tool_calls=None):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                role="assistant",
                                content=content,
                                tool_calls=tool_calls,
                            )
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                )

            tool_call = SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(
                    name="update_file_lines",
                    arguments=json.dumps(
                        {
                            "file_path": "pkg/mod.py",
                            "start_line": 1,
                            "end_line": 2,
                            "new_content": "def value():\nreturn 2\n",
                        }
                    ),
                ),
            )
            client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=mock.Mock(
                            side_effect=[
                                fake_response(tool_calls=[tool_call]),
                                fake_response(content="<output>repair observed rollback</output>"),
                            ]
                        )
                    )
                )
            )
            backend = OpenAIBackend(
                api_key="secret",
                api_base="https://api.openai.com/v1",
                deployment_name="gpt-5.4-2026-03-05",
                tool_approval_mode="auto-edit",
            )
            backend.client = client
            backend.workspace_dir = tmpdir
            backend.allowed_artifacts = ["pkg/mod.py"]
            backend.repair_diagnostics_text = "Repair diagnostics:\nUnit test validation failed for tests.test_mod.test_value"

            response = backend.chat_with_tools(
                [{"role": "user", "content": "repair pkg/mod.py"}],
                build_file_tools(tmpdir),
            )

            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)
            self.assertEqual(response.content, "<output>repair observed rollback</output>")
            self.assertTrue(response.raw["tool_results"][0]["rolled_back"])
            self.assertEqual(response.raw["tool_results"][0]["validation_status"], "rolled_back")
            self.assertIn("contractcoding_repair_validation", response.raw["tool_results"][0]["output"])
            self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_runtime_task_index_is_thin_and_status_accepts_task_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                if item.kind == "doc" and item.target_artifacts:
                    path = os.path.join(tmpdir, item.target_artifacts[0])
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write("# Runtime Notes\n\nThis document explains the Runtime V4 task index, contract, and verification flow in a concise way.\n")
                return {"output": "ok"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            result = engine.run_auto("Write a short document explaining the ContractCoding Runtime V4")
            task = store.get_task(result.task_id)
            status = engine.status(result.task_id)
            contract_payload = status["contract"].to_record()

            self.assertIsNotNone(task)
            self.assertEqual(task.prompt, "Write a short document explaining the ContractCoding Runtime V4")
            self.assertEqual(task.active_run_id, result.run_id)
            self.assertIn("status", task.status_summary)
            self.assertNotIn("work_items", task.status_summary)
            self.assertEqual(status["run"].id, result.run_id)
            self.assertFalse(any("status" in item or "evidence" in item for item in contract_payload["work_items"]))

    def test_run_auto_resumes_active_run_for_same_workspace_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            first = engine.run_auto("Create math_tools.py", max_steps=0)
            second = engine.run_auto("Create math_tools.py", max_steps=0)

            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(first.task_id, second.task_id)
            self.assertEqual(len(store.list_tasks(limit=20)), 1)

    def test_auto_run_max_steps_pauses_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                for artifact in item.target_artifacts:
                    path = os.path.join(tmpdir, artifact)
                    os.makedirs(os.path.dirname(path) or tmpdir, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write("VALUE = 1\n")
                return {"output": "ok"}

            engine = RunEngine(config=config, store=store, step_executor=executor)

            result = engine.run_auto("Create math_tools.py and text_tools.py", max_steps=1)
            run = store.get_run(result.run_id)

            self.assertEqual(result.status, "PAUSED")
            self.assertEqual(run.metadata.get("reason"), "max_steps_reached")
            self.assertNotEqual(run.status, "FAILED")

    def test_auto_run_max_steps_is_hard_budget_across_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            calls = {"count": 0}

            def executor(item, agent_name, state):
                calls["count"] += 1
                return {"output": "failed local validation", "validation_errors": ["simulated repairable failure"]}

            engine = RunEngine(config=config, store=store, step_executor=executor)

            result = engine.run_auto("Create math_tools.py", max_steps=1)
            run = store.get_run(result.run_id)

            self.assertEqual(result.status, "PAUSED")
            self.assertEqual(calls["count"], 1)
            self.assertEqual(store.count_steps(result.run_id), 1)
            self.assertEqual(run.metadata.get("steps_executed"), 1)

    def test_auto_run_zero_max_steps_plans_without_dispatching_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            calls = {"count": 0}

            def executor(item, agent_name, state):
                calls["count"] += 1
                return {"output": "should not dispatch"}

            engine = RunEngine(config=config, store=store, step_executor=executor)

            result = engine.run_auto("Create math_tools.py and text_tools.py", max_steps=0)

            self.assertEqual(result.status, "PAUSED")
            self.assertEqual(calls["count"], 0)
            self.assertGreater(len(store.list_work_items(result.run_id)), 0)
            self.assertEqual(store.latest_steps(result.run_id, limit=10), [])

    def test_openai_first_offline_e2e_completes_multifile_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), LLM_BACKEND="openai")
            store = RunStore.for_workspace(tmpdir)

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                workspace = get_current_workspace(tmpdir)
                for artifact in item.target_artifacts:
                    path = os.path.join(workspace, artifact)
                    if artifact == "math_tools.py":
                        content = (
                            "def add(a, b):\n    return a + b\n\n"
                            "def subtract(a, b):\n    return a - b\n\n"
                            "def multiply(a, b):\n    return a * b\n\n"
                            "def divide(a, b):\n"
                            "    if b == 0:\n        raise ValueError('division by zero')\n"
                            "    return a / b\n"
                        )
                    elif artifact == "text_tools.py":
                        content = (
                            "import re\n\n"
                            "def slugify(text):\n"
                            "    return re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')\n\n"
                            "def word_count(text):\n"
                            "    return len([part for part in str(text).split() if part])\n"
                        )
                    else:
                        content = "VALUE = 1\n"
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(content)
                return GeneralState(
                    task=state.task,
                    sub_task=state.sub_task,
                    role=agent_name,
                    output="implemented",
                    task_requirements={
                        "llm_observability": {
                            "backend": "openai",
                            "prompt_tokens": 20,
                            "completion_tokens": 10,
                            "tool_result_count": len(item.target_artifacts),
                        }
                    },
                )

            engine = RunEngine(config=config, store=store, step_executor=executor)
            result = engine.run_auto("Create two independent Python utility modules")

            self.assertEqual(result.status, "COMPLETED")
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "math_tools.py")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "text_tools.py")))
            self.assertTrue(any(event.event_type == "llm_observed" for event in store.list_events(result.run_id, limit=100)))
            self.assertIn("LLM:", result.report)

    def test_generic_contracts_get_delivery_type_and_system_gates(self):
        cases = [
            ("Write a document about agent team tradeoffs", "doc"),
            ("Analyze this CSV dataset and produce a data report", "data"),
            ("Create an ops deployment dry-run plan with rollback notes", "ops"),
            ("Research long-running agents and write up sourced claims", "research"),
        ]
        for goal, delivery_type in cases:
            with self.subTest(goal=goal):
                contract = ContractCompiler().compile(goal)

                self.assertEqual(contract.metadata["architecture"], "phase-contract-harness-v8")
                self.assertEqual(contract.metadata["delivery_type"], delivery_type)
                self.assertTrue(contract.team_gates)
                self.assertIsNotNone(contract.final_gate)
                self.assertFalse(any(item.kind == "eval" for item in contract.work_items))

    def test_research_with_evaluation_rubric_is_not_misclassified_as_eval(self):
        contract = ContractCompiler().compile(
            "Research retry strategies and write a technical brief with an evaluation rubric."
        )

        self.assertEqual(contract.metadata["delivery_type"], "research")
        self.assertIn("research:source-notes", contract.item_by_id())
        self.assertIn("doc:final-artifact", contract.item_by_id())

    def test_minimal_hooks_and_settings_affect_next_wave_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_dir = os.path.join(tmpdir, ".contractcoding")
            os.makedirs(settings_dir, exist_ok=True)
            with open(os.path.join(settings_dir, "settings.json"), "w", encoding="utf-8") as handle:
                json.dump({"max_parallel_teams": 1, "max_parallel_items_per_team": 1, "hooks_enabled": True}, handle)

            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            seen = []

            def executor(item, agent_name, state):
                seen.append(item.id)
                if item.target_artifacts:
                    workspace = get_current_workspace(tmpdir)
                    path = os.path.join(workspace, item.target_artifacts[0])
                    os.makedirs(os.path.dirname(path) or workspace, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write("VALUE = 1\n")
                return {"output": "ok"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            run_id = engine.start("Implement alpha.py and beta.py")
            engine.resume(run_id, max_steps=1)
            events = [event.event_type for event in store.list_events(run_id, limit=50)]

            self.assertEqual(seen, ["coding:alpha.py"])
            self.assertIn("hook:before_team_dispatch", events)
            self.assertEqual(engine.scheduler.runtime_overrides["max_parallel_items_per_team"], 1)

    def test_run_report_and_graph_explain_gates_and_parallelism(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            contract = ContractCompiler().compile("Create two independent Python utility modules")
            run_id = engine.start("demo", contract=contract, run_immediately=False)

            graph = engine.graph(run_id)
            report = engine.report(run_id, max_lines=8)

            self.assertIn("gates", graph)
            self.assertTrue(any(item["gate_id"] == "team:utils" for item in graph["gates"]))
            self.assertTrue(any(wave["parallel_reason"] for wave in graph["ready_waves"]))
            self.assertIn("Phase:", report)
            self.assertIn("Team gates:", report)
            self.assertIn("Final gate:", report)

    def test_auto_run_steward_completes_without_manual_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                if item.kind == "eval" and item.target_artifacts:
                    target = os.path.join(tmpdir, item.target_artifacts[0])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "w", encoding="utf-8") as handle:
                        json.dump({"ok": True, "metrics": {"completion": 1}}, handle)
                return {"output": "ok"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            result = engine.run_auto("Evaluate completion rate")

            self.assertEqual(result.status, "COMPLETED")
            self.assertEqual(result.replans, 0)
            self.assertIn("VERIFIED=1", result.report)

    def test_auto_run_steward_infra_retry_does_not_consume_contract_replan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                AUTO_INFRA_RETRY_MAX=2,
                AUTO_CONTRACT_REPLAN_MAX=0,
            )
            store = RunStore.for_workspace(tmpdir)
            calls = {"implementation": 0}

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                calls["implementation"] += 1
                if calls["implementation"] == 1:
                    raise RuntimeError(".contractcoding/runs.sqlite-journal disappeared during sandbox snapshot")
                with open(os.path.join(tmpdir, "demo.py"), "w", encoding="utf-8") as handle:
                    handle.write("VALUE = 1\n")
                return {"output": "created demo.py"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            result = engine.run_auto("Implement demo.py")

            self.assertEqual(result.status, "COMPLETED")
            self.assertEqual(result.replans, 0)
            metadata = store.get_run(result.run_id).metadata
            self.assertEqual(metadata["autonomy_guardrails"]["infra_retries"]["coding:demo.py"], 1)

    def test_run_engine_generates_interface_artifacts_without_llm(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            calls = []

            def executor(item, agent_name, state):
                calls.append((item.id, agent_name))
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                return {"output": "should not run for interface items"}

            contract = ContractCompiler().compile(
                "Build a large Python package project with pkg/core/engine.py pkg/core/state.py "
                "pkg/models/user.py pkg/models/resource.py pkg/systems/economy.py pkg/systems/building.py "
                "pkg/ai/planner.py pkg/io/save_load.py pkg/io/scenarios.py pkg/cli/main.py tests/test_core.py tests/test_io.py"
            )
            engine = RunEngine(config=config, store=store, step_executor=executor)
            run_id = engine.start("demo", contract=contract)
            engine.resume(run_id, max_steps=10)

            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "scaffolds", "ai.json")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "interfaces", "ai.json")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "interface_tests", "ai.json")))
            self.assertNotIn(("interface:ai", "Architect"), calls)

    def test_auto_run_steward_item_repair_fixes_import_failure_without_replan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                AUTO_ITEM_REPAIR_MAX=2,
                AUTO_CONTRACT_REPLAN_MAX=0,
            )
            store = RunStore.for_workspace(tmpdir)
            calls = {"implementation": 0}

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {
                        "output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'
                    }
                calls["implementation"] += 1
                with open(os.path.join(tmpdir, "broken.py"), "w", encoding="utf-8") as handle:
                    if calls["implementation"] == 1:
                        handle.write("VALUE = missing_name\n")
                    else:
                        handle.write("VALUE = 1\n")
                return {"output": "created broken.py"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            result = engine.run_auto("Implement broken.py")

            self.assertEqual(result.status, "COMPLETED")
            self.assertEqual(result.replans, 0)
            metadata = store.get_run(result.run_id).metadata
            self.assertEqual(metadata["autonomy_guardrails"]["item_repairs"]["coding:broken.py"], 1)

    def test_item_repair_reopens_with_structured_self_check_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile("Implement broken.py")
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            store.update_work_item_status(
                run_id,
                "coding:broken.py",
                "BLOCKED",
                evidence=["Import validation failed for broken.py: NameError: name 'missing_name' is not defined"],
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 0},
            )
            item = store.get_work_item(run_id, "coding:broken.py")

            self.assertTrue(recovered)
            self.assertEqual(item.status, "READY")
            self.assertEqual(item.inputs["latest_diagnostic"]["failure_kind"], "import_error")
            self.assertIn("repair_instruction", item.inputs)

    def test_contract_compiler_scales_game_contract_by_complexity(self):
        simple = ContractCompiler().compile("Create a simple Python terminal number guessing game")
        simple_items = {item.target_artifacts[0]: item for item in simple.work_items if item.kind == "coding"}

        self.assertEqual(set(simple_items), {"game_engine.py", "main.py"})
        self.assertEqual(simple_items["main.py"].depends_on, ["coding:game_engine.py"])
        self.assertTrue(any("high/low/correct" in criterion for criterion in simple_items["game_engine.py"].acceptance_criteria))
        self.assertEqual({gate.scope_id for gate in simple.team_gates}, {"game"})
        self.assertIn("test_game_engine.py", simple.team_gates[0].test_artifacts)

        medium = ContractCompiler().compile("Create a medium-difficulty Python terminal Tic Tac Toe game")
        medium_items = {item.target_artifacts[0]: item for item in medium.work_items if item.kind == "coding"}

        self.assertEqual(set(medium_items), {"game_engine.py", "ai_player.py", "main.py"})
        self.assertEqual(medium_items["main.py"].depends_on, ["coding:game_engine.py", "coding:ai_player.py"])
        self.assertTrue(any("minimax" in criterion for criterion in medium_items["ai_player.py"].acceptance_criteria))
        self.assertTrue(any("post-game" in criterion for criterion in medium_items["game_engine.py"].acceptance_criteria))
        self.assertIn("win/loss/draw behavior", medium.team_gates[0].test_plan["required_behaviors"])

        hard = ContractCompiler().compile("Create a hard Python terminal Minesweeper game")
        hard_items = {item.target_artifacts[0]: item for item in hard.work_items if item.kind == "coding"}

        self.assertEqual(set(hard_items), {"game_engine.py", "board_generator.py", "main.py"})
        self.assertEqual(
            hard_items["main.py"].depends_on,
            ["coding:game_engine.py", "coding:board_generator.py"],
        )
        self.assertTrue(any(interface.get("name") == "generate_mines" for interface in hard_items["board_generator.py"].provided_interfaces))
        self.assertTrue(any("first-click" in criterion for criterion in hard_items["game_engine.py"].acceptance_criteria))

    def test_scheduler_parallel_slots_scale_with_game_contract_complexity(self):
        cases = [
            ("Create a simple Python terminal number guessing game", 2),
            ("Create a medium-difficulty Python terminal Tic Tac Toe game", 3),
            ("Create a hard Python terminal Minesweeper game", 3),
        ]
        for goal, expected_slots in cases:
            with self.subTest(goal=goal), tempfile.TemporaryDirectory() as tmpdir:
                contract = ContractCompiler().compile(goal)
                store = RunStore.for_workspace(tmpdir)
                run_id = store.create_run("demo", tmpdir, contract=contract)

                waves = Scheduler(store).next_wave(run_id)
                game_wave = next(wave for wave in waves if wave.scope.id == "game")

                self.assertEqual(game_wave.wave_kind, "implementation")
                self.assertEqual(game_wave.parallel_slots, expected_slots)
                self.assertNotIn("coding:test_game_engine.py", {item.id for item in game_wave.items})

    def test_scheduler_builds_parallel_team_and_item_waves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {"id": "core", "type": "code_module", "label": "Core"},
                        {"id": "docs", "type": "doc", "label": "Docs"},
                    ],
                    "work_items": [
                        {
                            "id": "code:a",
                            "kind": "coding",
                            "title": "A",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "core",
                            "target_artifacts": ["a.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "code:b",
                            "kind": "coding",
                            "title": "B",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "core",
                            "target_artifacts": ["b.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "doc:readme",
                            "kind": "doc",
                            "title": "Readme",
                            "owner_profile": "Technical_Writer",
                            "scope_id": "docs",
                            "target_artifacts": ["README.md"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)

            waves = Scheduler(store).next_wave(run_id)
            by_scope = {wave.scope.id: wave for wave in waves}

            self.assertEqual({item.id for item in by_scope["core"].items}, {"code:a", "code:b"})
            self.assertEqual(by_scope["core"].parallel_slots, 2)
            self.assertEqual(by_scope["core"].execution_plane, "worktree")
            self.assertIn("docs", by_scope)

    def test_scheduler_allows_interface_ready_parallel_implementation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile("Create a Python terminal Connect Four game")
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)

            waves = Scheduler(store).next_wave(run_id)
            game_wave = next(wave for wave in waves if wave.scope.id == "game")

            self.assertEqual(game_wave.wave_kind, "implementation")
            self.assertIn("coding:main.py", {item.id for item in game_wave.items})
            self.assertIn("coding:game_engine.py", {item.id for item in game_wave.items})
            self.assertEqual(game_wave.parallel_slots, 3)

    def test_scheduler_serializes_conflicting_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "core", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "a",
                            "kind": "coding",
                            "title": "A",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "core",
                            "target_artifacts": ["shared.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "b",
                            "kind": "coding",
                            "title": "B",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "core",
                            "target_artifacts": ["shared.py"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)

            waves = Scheduler(store).next_wave(run_id)

            self.assertEqual(len(waves), 1)
            self.assertEqual(len(waves[0].items), 1)

    def test_run_engine_resume_does_not_repeat_completed_work(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            calls = []

            def executor(item, agent_name, state):
                calls.append((item.id, agent_name))
                return GeneralState(
                    task=state.task,
                    sub_task=state.sub_task,
                    role=agent_name,
                    thinking="done",
                    output=f"completed {item.id}",
                    next_agents=[],
                )

            engine = RunEngine(config=config, store=store, step_executor=executor)
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(id="a", kind="doc", title="A", owner_profile="Technical_Writer", status="READY"),
                    WorkItem(
                        id="b",
                        kind="doc",
                        title="B",
                        owner_profile="Technical_Writer",
                        depends_on=["a"],
                        status="TODO",
                    ),
                ],
            )

            engine.resume(run_id, max_steps=1)
            engine.resume(run_id, max_steps=1)
            engine.resume(run_id, max_steps=1)

            self.assertEqual([call[0] for call in calls], ["a", "b"])
            self.assertEqual(store.get_work_item(run_id, "a").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "b").status, "VERIFIED")

    def test_run_engine_replan_records_new_contract_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(id="a", kind="doc", title="A", owner_profile="Technical_Writer", status="ERROR"),
                    WorkItem(id="b", kind="doc", title="B", owner_profile="Technical_Writer", status="VERIFIED"),
                ],
            )

            contract = engine.replan(run_id, "A failed because required evidence was missing.")

            self.assertEqual(contract.metadata["planner_mode"], "replan")
            self.assertEqual(store.get_work_item(run_id, "a").status, "READY")
            self.assertEqual(store.get_work_item(run_id, "b").status, "VERIFIED")
            self.assertIn("replan_feedback", store.get_work_item(run_id, "a").inputs)

    def test_run_health_classifies_blocked_items_for_item_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(id="a", kind="doc", title="A", owner_profile="Technical_Writer", status="ERROR")
                ],
            )

            health = engine.status(run_id)["health"]

            self.assertEqual(health.status, "WARN")
            self.assertFalse(health.replan_recommended)
            self.assertEqual(health.diagnostics[0].recovery_action, "item_repair")
            self.assertEqual(health.diagnostics[0].failure_kind, "item_quality")

    def test_run_health_reads_llm_infra_failure_from_step_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(
                        id="coding:demo.py",
                        kind="coding",
                        title="Demo",
                        owner_profile="Backend_Engineer",
                        target_artifacts=["demo.py"],
                    )
                ],
            )
            step_id = store.create_step(run_id, "coding:demo.py", "Backend_Engineer")
            store.finish_step(
                step_id,
                "ERROR",
                output_payload={
                    "output": "OpenAI infrastructure failure: request timed out",
                    "task_requirements": {
                        "llm_observability": {
                            "backend": "openai",
                            "infra_failure": True,
                            "failure_kind": "infra",
                        }
                    },
                    "validation_errors": ["Required target file was not created or updated: demo.py"],
                },
                error="Required target file was not created or updated: demo.py",
            )
            store.update_work_item_status(
                run_id,
                "coding:demo.py",
                "BLOCKED",
                evidence=["Required target file was not created or updated: demo.py"],
            )

            health = engine.status(run_id)["health"]

            self.assertTrue(any(d.failure_kind == "infra" for d in health.diagnostics))
            self.assertTrue(any(d.recovery_action == "infra_retry" for d in health.diagnostics))

    def test_steward_resets_gate_when_final_gate_finds_all_tests_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {"id": "pkg", "type": "code_module", "artifacts": ["pkg/__init__.py"]},
                        {"id": "tests", "type": "tests", "artifacts": ["tests/test_pkg.py"]},
                        {"id": "integration", "type": "eval"},
                    ],
                    "work_items": [
                        {
                            "id": "coding:pkg/__init__.py",
                            "kind": "coding",
                            "title": "Package",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/__init__.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "coding:tests/test_pkg.py",
                            "kind": "coding",
                            "title": "Tests",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "tests",
                            "status": "VERIFIED",
                            "target_artifacts": ["tests/test_pkg.py"],
                            "acceptance_criteria": ["tests exercise package"],
                        },
                    ],
                    "team_gates": [{"scope_id": "pkg"}, {"scope_id": "tests", "test_artifacts": ["tests/test_pkg.py"]}],
                    "final_gate": {
                        "required_artifacts": ["pkg/__init__.py", "tests/test_pkg.py"],
                        "python_artifacts": ["pkg/__init__.py", "tests/test_pkg.py"],
                        "requires_tests": True,
                    },
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            store.update_gate_status(run_id, "team:pkg", "PASSED")
            store.update_gate_status(run_id, "team:tests", "PASSED")
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=["Unittest discovery failed: no executable tests ran (ran=0, skipped=8)."],
            )
            step_id = store.create_step(run_id, "final", "FinalReviewer")
            store.finish_step(
                step_id,
                "ERROR",
                error="Unittest discovery failed: no executable tests ran (ran=0, skipped=8).",
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "contract_replan_limit": 2},
            )

            self.assertTrue(recovered)
            self.assertEqual(store.get_gate(run_id, "final").status, "PENDING")

    def test_scheduler_does_not_auto_retry_blocked_items_without_replan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "root", "type": "root"}],
                    "work_items": [
                        {
                            "id": "a",
                            "kind": "doc",
                            "title": "A",
                            "owner_profile": "Technical_Writer",
                            "status": "ERROR",
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            run_id = store.create_run(
                "demo",
                tmpdir,
                contract=contract,
            )

            self.assertEqual(Scheduler(store).next_wave(run_id), [])
            blocked = Scheduler(store).blocked_reasons(run_id)
            self.assertEqual(blocked[0].work_item_id, "a")
            self.assertIn("replan", blocked[0].reason)

    def test_run_engine_stops_after_failed_self_check_before_dependent_wave(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            calls = []

            def executor(item, agent_name, state):
                calls.append((item.id, agent_name))
                with open(os.path.join(tmpdir, "a.py"), "w", encoding="utf-8") as handle:
                    handle.write('def broken():\n    return "\n')
                return {"output": "created"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(
                        id="a",
                        kind="coding",
                        title="A",
                        owner_profile="Backend_Engineer",
                        status="READY",
                        target_artifacts=["a.py"],
                        acceptance_criteria=["accepted"],
                    ),
                    WorkItem(
                        id="b",
                        kind="coding",
                        title="B",
                        owner_profile="Backend_Engineer",
                        status="READY",
                        depends_on=["a"],
                        target_artifacts=["b.py"],
                        acceptance_criteria=["accepted"],
                    ),
                ],
            )

            run = engine.resume(run_id, max_steps=2)

            self.assertEqual(calls, [("a", "Backend_Engineer")])
            self.assertEqual(run.status, "BLOCKED")
            self.assertEqual(store.get_work_item(run_id, "a").status, "BLOCKED")
            self.assertEqual(store.get_work_item(run_id, "b").status, "READY")
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "b.py")))

    def test_expired_lease_allows_resume_without_duplicate_completed_work(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run(
                "demo",
                tmpdir,
                work_items=[WorkItem(id="a", kind="doc", title="A", owner_profile="Technical_Writer", status="READY")],
            )

            self.assertTrue(store.acquire_leases(run_id, "team-old", ["a"], lease_seconds=-1))
            self.assertEqual(store.active_leased_items(run_id), set())

    def test_resume_recovers_stale_running_item_without_active_lease(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
            )
            item = WorkItem(
                id="doc:summary",
                kind="doc",
                title="Summary",
                owner_profile="Technical_Writer",
                status="READY",
                acceptance_criteria=["done"],
            )
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "done"})
            run_id = engine.start("demo", initial_work_items=[item])
            stale_step = store.create_step(run_id, item.id, "Technical_Writer")
            store.update_work_item_status(run_id, item.id, "RUNNING")

            engine.resume(run_id, max_steps=1)

            recovered = store.get_work_item(run_id, item.id)
            self.assertEqual(recovered.status, "VERIFIED")
            self.assertEqual(store.latest_step_for_item(run_id, item.id).status, "COMPLETED")
            self.assertTrue(any(event.event_type == "stale_running_item_recovered" for event in store.list_events(run_id)))
            stale_rows = [step for step in store.latest_steps(run_id, limit=10) if step.id == stale_step]
            self.assertEqual(stale_rows[0].status, "ERROR")

    def test_resume_recovers_stale_running_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
            )
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "done"})
            contract = ContractCompiler().compile("Implement pkg/__init__.py tests/test_pkg.py")
            run_id = engine.start("demo", contract=contract)
            store.update_gate_status(run_id, "final", "RUNNING")

            engine.run_loop._recover_stale_running_gates(run_id)

            self.assertEqual(store.get_gate(run_id, "final").status, "PENDING")
            self.assertTrue(any(event.event_type == "stale_running_gate_recovered" for event in store.list_events(run_id)))

    def test_eval_suite_runner_records_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)

            def executor(item, agent_name, state):
                if agent_name == "Critic":
                    return {"output": '<verification>{"verdict":"pass","evidence":["ok"],"missing_evidence":[],"risks":[]}</verification>'}
                return {"output": "ok"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            runner = EvalSuiteRunner(engine)
            results = runner.run_cases([EvalCase(id="case-1", task="Evaluate completion rate")])
            rel_path = runner.write_results("unit-suite", results)

            self.assertEqual(results[0].case_id, "case-1")
            self.assertIn(results[0].status, {"RUNNING", "COMPLETED", "BLOCKED"})
            self.assertEqual(sum(results[0].metrics.values()), 1)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, rel_path)))
            with open(os.path.join(tmpdir, rel_path), encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertIn("repair_tickets", payload["summary"])
            self.assertIn("false_done_risks", payload["summary"])

    def test_run_monitor_writes_safe_structured_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secret = "sk-test-secret-value"
            with mock.patch.dict(os.environ, {"API_KEY": secret, "BASE_URL": "https://secret.example"}, clear=False):
                config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
                store = RunStore.for_workspace(tmpdir)
                engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "done"})
                contract = ContractCompiler().compile("Implement demo.py")
                run_id = engine.start("demo", contract=contract)
                step_id = store.create_step(run_id, "coding:demo.py", "Backend_Engineer")
                store.finish_step(
                    step_id,
                    "COMPLETED",
                    {
                        "llm_observability": {
                            "backend": "openai",
                            "prompt_tokens": 11,
                            "completion_tokens": 7,
                            "tool_result_count": 2,
                        }
                    },
                )

                snapshot = engine.monitor(run_id)

            self.assertEqual(snapshot["run"]["id"], run_id)
            self.assertIn("items", snapshot["counts"])
            self.assertIn("teams", snapshot)
            self.assertIn("gates", snapshot)
            self.assertIn("repair_tickets", snapshot)
            self.assertEqual(snapshot["llm_observability"]["prompt_tokens"], 11)
            rendered = json.dumps(snapshot)
            self.assertNotIn(secret, rendered)
            self.assertNotIn("https://secret.example", rendered)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "monitor", f"{run_id}.json")))

    def test_status_json_serializes_health_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(
                        id="coding:demo.py",
                        kind="coding",
                        title="Demo",
                        owner_profile="Backend_Engineer",
                        target_artifacts=["demo.py"],
                        status="BLOCKED",
                        evidence=["Import validation failed for demo.py: NameError"],
                    )
                ],
            )
            store.update_run_status(run_id, "BLOCKED")

            payload = _status_to_jsonable(engine.status(run_id))

            self.assertEqual(payload["health"]["diagnostics"][0]["code"], "work_item_blocked")
            self.assertIn("failure_kind", payload["health"]["diagnostics"][0])
            json.dumps(payload)

    def test_health_ignores_error_steps_after_item_is_verified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})
            run_id = engine.start(
                "demo",
                initial_work_items=[
                    WorkItem(
                        id="coding:demo.py",
                        kind="coding",
                        title="Demo",
                        owner_profile="Backend_Engineer",
                        target_artifacts=["demo.py"],
                        status="VERIFIED",
                    )
                ],
            )
            step_id = store.create_step(run_id, "coding:demo.py", "Backend_Engineer")
            store.finish_step(step_id, "ERROR", error="Invalid blocker: required artifacts are already allowed")
            store.update_run_status(run_id, "RUNNING")

            health = engine.status(run_id)["health"]

            self.assertFalse(any(diagnostic.code == "recent_step_error" for diagnostic in health.diagnostics))

    def test_default_real_task_eval_cases_cover_sizes_and_generic_kinds(self):
        cases = default_real_task_eval_cases("stress")
        tags = {tag for case in cases for tag in case.tags}

        self.assertTrue({"small", "medium", "large", "coding", "research", "data", "ops"}.issubset(tags))
        for case in cases:
            contract = ContractCompiler().compile(case.task)
            self.assertIn(contract.metadata["delivery_type"], {"coding", "research", "doc", "data", "ops", "eval", "mixed"})

        summary = EvalSummary(
            [
                type(
                    "SyntheticResult",
                    (),
                    {
                        "status": "COMPLETED",
                        "team_count": 2,
                        "artifact_count": 3,
                        "step_count": 4,
                        "repair_ticket_count": 0,
                        "gate_failure_count": 0,
                        "false_done_risk": False,
                        "llm_prompt_tokens": 0,
                        "llm_completion_tokens": 0,
                        "llm_tool_results": 0,
                    },
                )()
            ]
        )
        self.assertEqual(summary.to_metrics()["completed"], 1)
        self.assertEqual(summary.to_metrics()["teams"], 2)

    def test_test_command_discoverer_covers_common_project_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "tests", "test_demo.py"), "w", encoding="utf-8") as handle:
                handle.write("import unittest\n\nclass Demo(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
            with open(os.path.join(tmpdir, "package.json"), "w", encoding="utf-8") as handle:
                handle.write('{"scripts":{"test":"node --test"}}\n')
            with open(os.path.join(tmpdir, "Cargo.toml"), "w", encoding="utf-8") as handle:
                handle.write("[package]\nname='demo'\nversion='0.1.0'\nedition='2021'\n")
            with open(os.path.join(tmpdir, "go.mod"), "w", encoding="utf-8") as handle:
                handle.write("module example.com/demo\n")

            discovered = TestCommandDiscoverer(tmpdir, mode="auto").discover(requires_tests=True)
            commands = {command.name: command.command for command in discovered.commands}

            self.assertEqual(commands["unittest"][:3], [sys.executable, "-m", "unittest"])
            self.assertEqual(commands["npm"], ["npm", "test"])
            self.assertEqual(commands["cargo"], ["cargo", "test"])
            self.assertEqual(commands["go"], ["go", "test", "./..."])
            self.assertEqual(TestCommandDiscoverer(tmpdir, mode="off").discover(requires_tests=True).commands, [])
            python_only = TestCommandDiscoverer(tmpdir, mode="python-only").discover(requires_tests=True)
            self.assertEqual([command.name for command in python_only.commands], ["unittest"])

    def test_scope_local_test_auditor_rejects_unavailable_dependency_patch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "rulegrid"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "rulegrid", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("__version__ = '0.1.0'\n")
            with open(os.path.join(tmpdir, "rulegrid", "cli.py"), "w", encoding="utf-8") as handle:
                handle.write("def main(argv=None):\n    return 0\n")
            with open(os.path.join(tmpdir, "tests", "test_cli.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "from unittest import mock\n\n"
                    "def test_step():\n"
                    "    with mock.patch('rulegrid.core.engine.SimulationEngine'):\n"
                    "        pass\n"
                )

            audit = TestStrataAuditor(tmpdir).audit_scope_tests(
                scope_id="interface",
                test_artifacts=["tests/test_cli.py"],
                scope_artifacts=["rulegrid/cli.py"],
                dependency_scope_ids=["core"],
            )

            self.assertFalse(audit.ok)
            self.assertIn("unavailable dependency scope module `rulegrid.core.engine.SimulationEngine`", audit.errors[0])

            os.makedirs(os.path.join(tmpdir, "rulegrid", "core"), exist_ok=True)
            with open(os.path.join(tmpdir, "rulegrid", "core", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "rulegrid", "core", "engine.py"), "w", encoding="utf-8") as handle:
                handle.write("class SimulationEngine: pass\n")

            repaired = TestStrataAuditor(tmpdir).audit_scope_tests(
                scope_id="interface",
                test_artifacts=["tests/test_cli.py"],
                scope_artifacts=["rulegrid/cli.py"],
                dependency_scope_ids=["core"],
            )
            self.assertTrue(repaired.ok)

    def test_gate_dependency_scopes_defer_team_gate_until_dependencies_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="root", type="root"),
                    WorkScope(id="package", type="package"),
                    WorkScope(id="core", type="code_module"),
                    WorkScope(id="interface", type="code_module"),
                ],
                work_items=[
                    WorkItem(id="implement:interface", kind="coding", scope_id="interface", status="VERIFIED", target_artifacts=["pkg/cli.py"], acceptance_criteria=["done"]),
                ],
                team_gates=[
                    TeamGateSpec(scope_id="package"),
                    TeamGateSpec(scope_id="core"),
                    TeamGateSpec(scope_id="interface", test_plan={"dependency_scope_ids": ["package", "core"]}),
                ],
                acceptance_criteria=["done"],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            runner = GateRunner(config=config, store=store, team_runtime=TeamRuntime(config=config, store=store))
            gate = next(gate for gate in contract.team_gates if gate.scope_id == "interface")

            self.assertFalse(runner._gate_dependencies_passed(run_id, gate))
            store.update_gate_status(run_id, "team:package", "PASSED")
            self.assertFalse(runner._gate_dependencies_passed(run_id, gate))
            store.update_gate_status(run_id, "team:core", "PASSED")
            self.assertTrue(runner._gate_dependencies_passed(run_id, gate))

    def test_team_executor_does_not_mark_validation_errors_done(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "utils", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "coding:missing.py",
                            "kind": "coding",
                            "title": "Missing file",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "utils",
                            "target_artifacts": ["missing.py"],
                            "acceptance_criteria": ["file exists"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            wave = Scheduler(store).next_wave(run_id)[0]

            def executor(item, agent_name, state):
                return {"output": "failed", "validation_errors": ["Required target file was not created."]}

            result = TeamExecutor(config=config, store=store, step_executor=executor).execute(run, wave)

            self.assertFalse(result.ok)
            self.assertEqual(store.get_work_item(run_id, "coding:missing.py").status, "BLOCKED")
            self.assertEqual(store.latest_steps(run_id, limit=1)[0].status, "ERROR")

    def test_team_executor_rejects_self_check_of_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "utils", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "coding:missing.py",
                            "kind": "coding",
                            "title": "Missing file",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "utils",
                            "target_artifacts": ["missing.py"],
                            "acceptance_criteria": ["file exists"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            wave = Scheduler(store).next_wave(run_id)[0]

            result = TeamExecutor(config=config, store=store, step_executor=lambda *_: {"output": "verified"}).execute(
                run,
                wave,
            )

            self.assertEqual(wave.wave_kind, "implementation")
            self.assertFalse(result.ok)
            self.assertEqual(store.get_work_item(run_id, "coding:missing.py").status, "BLOCKED")

    def test_team_executor_implementation_prompt_includes_contract_slice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "demo.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "utils", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "coding:demo.py",
                            "kind": "coding",
                            "title": "Demo",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "utils",
                            "target_artifacts": ["demo.py"],
                            "acceptance_criteria": ["demo.py defines VALUE"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            wave = Scheduler(store).next_wave(run_id)[0]

            prompt = TeamExecutor(config=config, store=store)._build_sub_task(wave, wave.items[0])

            self.assertIn("Acceptance criteria", prompt)
            self.assertIn("demo.py defines VALUE", prompt)
            self.assertIn("Target files in this module wave", prompt)
            self.assertIn("demo.py", prompt)

    def test_team_executor_rejects_python_syntax_errors_after_implementation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "utils", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "coding:broken.py",
                            "kind": "coding",
                            "title": "Broken file",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "utils",
                            "target_artifacts": ["broken.py"],
                            "acceptance_criteria": ["file compiles"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            wave = Scheduler(store).next_wave(run_id)[0]

            def executor(item, agent_name, state):
                with open(os.path.join(tmpdir, "broken.py"), "w", encoding="utf-8") as handle:
                    handle.write('def render():\n    return "\n')
                return {"output": "created broken.py"}

            result = TeamExecutor(config=config, store=store, step_executor=executor).execute(run, wave)

            self.assertFalse(result.ok)
            self.assertEqual(store.get_work_item(run_id, "coding:broken.py").status, "BLOCKED")
            self.assertIn("Python syntax validation failed", store.latest_steps(run_id, limit=1)[0].error)

    def test_team_executor_rejects_python_import_errors_after_compile_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "utils", "type": "code_module"}],
                    "work_items": [
                        {
                            "id": "coding:import_broken.py",
                            "kind": "coding",
                            "title": "Import-broken file",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "utils",
                            "target_artifacts": ["import_broken.py"],
                            "acceptance_criteria": ["file imports"],
                        }
                    ],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            wave = Scheduler(store).next_wave(run_id)[0]

            def executor(item, agent_name, state):
                with open(os.path.join(tmpdir, "import_broken.py"), "w", encoding="utf-8") as handle:
                    handle.write("DEFAULT_EVENT_DECK = EventDeck([])\n\nclass EventDeck:\n    pass\n")
                return {"output": "created import_broken.py"}

            result = TeamExecutor(config=config, store=store, step_executor=executor).execute(run, wave)

            self.assertFalse(result.ok)
            self.assertEqual(store.get_work_item(run_id, "coding:import_broken.py").status, "BLOCKED")
            self.assertIn("Python import validation failed", store.latest_steps(run_id, limit=1)[0].error)

    def test_integration_gate_fails_when_required_tests_are_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            gate = FinalGateSpec(
                required_artifacts=["pkg/__init__.py"],
                python_artifacts=["pkg/__init__.py"],
                package_roots=["pkg"],
                requires_tests=True,
            )

            result = GateChecker(tmpdir).check_final_gate(gate)

            self.assertFalse(result.ok)
            self.assertTrue(any("Required tests" in error for error in result.errors))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "integration_report.json")))

    def test_final_gate_blackbox_behavior_catches_missing_module_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "interface"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('"""demo package."""\n')
            with open(os.path.join(tmpdir, "pkg", "interface", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('"""interface package."""\n')
            with open(os.path.join(tmpdir, "pkg", "interface", "cli.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "def main(argv=None):\n"
                    "    print('usage: demo')\n"
                    "    return 0\n"
                )

            gate = FinalGateSpec(
                required_artifacts=[
                    "pkg/__init__.py",
                    "pkg/interface/__init__.py",
                    "pkg/interface/cli.py",
                ],
                python_artifacts=[
                    "pkg/__init__.py",
                    "pkg/interface/__init__.py",
                    "pkg/interface/cli.py",
                ],
                package_roots=["pkg"],
                product_behavior={
                    "capabilities": ["cli_blackbox"],
                    "blackbox_commands": [
                        {
                            "id": "pkg.interface.cli:help",
                            "argv": ["{python}", "-m", "pkg.interface.cli", "--help"],
                            "expected_returncode": 0,
                            "stdout_contains_any": ["usage", "help"],
                            "require_output": True,
                        }
                    ],
                },
            )

            result = GateChecker(tmpdir).check_final_gate(gate)

            self.assertFalse(result.ok)
            self.assertTrue(any("Product behavior blackbox" in error for error in result.errors))

    def test_final_gate_blackbox_behavior_accepts_real_module_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "interface"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('"""demo package."""\n')
            with open(os.path.join(tmpdir, "pkg", "interface", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('"""interface package."""\n')
            with open(os.path.join(tmpdir, "pkg", "interface", "cli.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import argparse\n\n"
                    "def main(argv=None):\n"
                    "    parser = argparse.ArgumentParser(prog='demo')\n"
                    "    parser.parse_args(argv)\n"
                    "    return 0\n\n"
                    "if __name__ == '__main__':\n"
                    "    raise SystemExit(main())\n"
                )

            gate = FinalGateSpec(
                required_artifacts=[
                    "pkg/__init__.py",
                    "pkg/interface/__init__.py",
                    "pkg/interface/cli.py",
                ],
                python_artifacts=[
                    "pkg/__init__.py",
                    "pkg/interface/__init__.py",
                    "pkg/interface/cli.py",
                ],
                package_roots=["pkg"],
                product_behavior={
                    "capabilities": ["cli_blackbox"],
                    "blackbox_commands": [
                        {
                            "id": "pkg.interface.cli:help",
                            "argv": ["{python}", "-m", "pkg.interface.cli", "--help"],
                            "expected_returncode": 0,
                            "stdout_contains_any": ["usage", "help"],
                            "require_output": True,
                        }
                    ],
                },
            )

            result = GateChecker(tmpdir).check_final_gate(gate)

            self.assertTrue(result.ok, result.errors)
            self.assertTrue(any("Product behavior blackbox" in line for line in result.evidence))

    def test_final_gate_semantic_behavior_requires_named_capability_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "core"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('"""demo package."""\n')
            with open(os.path.join(tmpdir, "pkg", "core", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('"""core package."""\n')
            with open(os.path.join(tmpdir, "pkg", "core", "failures.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "class FailureEngine:\n"
                    "    def failure_recorded(self):\n"
                    "        return {'event': 'failure_recorded'}\n\n"
                    "    def failure_repaired(self):\n"
                    "        return {'event': 'failure_repaired'}\n"
                )
            with open(os.path.join(tmpdir, "tests", "test_integration.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import unittest\n\n"
                    "class IntegrationTests(unittest.TestCase):\n"
                    "    def test_smoke_behavior(self):\n"
                    "        self.assertTrue(True)\n"
                )

            gate = FinalGateSpec(
                required_artifacts=[
                    "pkg/__init__.py",
                    "pkg/core/__init__.py",
                    "pkg/core/failures.py",
                    "tests/test_integration.py",
                ],
                python_artifacts=[
                    "pkg/__init__.py",
                    "pkg/core/__init__.py",
                    "pkg/core/failures.py",
                    "tests/test_integration.py",
                ],
                package_roots=["pkg"],
                requires_tests=True,
                product_behavior={
                    "capabilities": ["failure_repair"],
                    "semantic_requirements": [
                        {
                            "id": "failure_repair_is_semantic_not_note_text",
                            "required_terms": ["failure_recorded", "failure_repaired"],
                            "test_artifacts": ["tests/test_integration.py"],
                            "implementation_artifacts": ["pkg/core/failures.py"],
                            "must_appear_in_tests": True,
                            "must_appear_in_implementation": True,
                        }
                    ],
                },
            )

            result = GateChecker(tmpdir).check_final_gate(gate)

            self.assertFalse(result.ok)
            self.assertTrue(any("Semantic behavior" in error for error in result.errors))

    def test_final_gate_generates_owned_integration_tests_before_verification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="root", type="root", label="Root")],
                work_items=[],
                team_gates=[],
                final_gate=FinalGateSpec(
                    required_artifacts=["pkg/__init__.py", "tests/test_integration.py"],
                    python_artifacts=["pkg/__init__.py", "tests/test_integration.py"],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
                acceptance_criteria=["done"],
            )
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)

            def executor(item, agent_name, state):
                self.assertEqual(item.id, "gate:final:tests")
                target = os.path.join(tmpdir, item.target_artifacts[0])
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8") as handle:
                    handle.write(
                        "import unittest\n"
                        "import pkg\n\n"
                        "class IntegrationTests(unittest.TestCase):\n"
                        "    def test_pkg_value(self):\n"
                        "        self.assertEqual(pkg.VALUE, 1)\n"
                    )
                return {"output": "created integration tests"}

            engine = RunEngine(config=config, store=store, step_executor=executor)
            result = engine.gate_runner.run_final_gate_if_ready(run, contract)

            self.assertTrue(result.ok)
            self.assertEqual(store.get_gate(run_id, "final").status, "PASSED")
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "tests", "test_integration.py")))

    def test_missing_test_artifact_routes_to_test_regeneration(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_TEST

        route = FailureRouter().classify_diagnostics(
            [
                DiagnosticRecord(
                    gate_id="team:core",
                    scope_id="core",
                    failure_kind="missing_artifact",
                    affected_artifacts=["tests/test_engine.py"],
                )
            ]
        )

        self.assertEqual(route.action, RECOVER_TEST)

    def test_final_missing_test_artifacts_do_not_route_to_implementation(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_TEST

        diagnostic = DiagnosticBuilder.from_final_gate_failure(
            errors=[
                "Integration gate failed: target artifact(s) missing: "
                "tests/test_domain.py, tests/test_engine.py, tests/test_ai.py"
            ],
            required_artifacts=[
                "pkg/domain/resources.py",
                "pkg/core/engine.py",
                "tests/test_domain.py",
                "tests/test_engine.py",
                "tests/test_ai.py",
            ],
            artifact_scope_map={
                "pkg/domain/resources.py": "domain",
                "pkg/core/engine.py": "core",
                "tests/test_domain.py": "domain",
                "tests/test_engine.py": "core",
                "tests/test_ai.py": "ai",
            },
        )[0]
        route = FailureRouter().classify_diagnostics([diagnostic])

        self.assertEqual(diagnostic.failure_kind, "missing_test_artifact")
        self.assertEqual(diagnostic.recovery_action, "test_regeneration")
        self.assertEqual(diagnostic.primary_scope, "tests")
        self.assertEqual(
            diagnostic.test_artifacts,
            ["tests/test_domain.py", "tests/test_engine.py", "tests/test_ai.py"],
        )
        self.assertEqual(diagnostic.suspected_implementation_artifacts, [])
        self.assertEqual(route.action, RECOVER_TEST)

    def test_mixed_final_missing_tests_and_import_error_routes_to_implementation_first(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_IMPLEMENTATION

        route = FailureRouter().classify_diagnostics(
            [
                DiagnosticRecord(
                    gate_id="final",
                    scope_id="integration",
                    failure_kind="missing_test_artifact",
                    test_artifacts=["tests/test_core.py"],
                    recovery_action="test_regeneration",
                    primary_scope="tests",
                ),
                DiagnosticRecord(
                    gate_id="final",
                    scope_id="integration",
                    failure_kind="import_error",
                    traceback_excerpt="NameError: name '_read_scenario' is not defined",
                    affected_artifacts=["pkg/interface/cli.py"],
                    suspected_implementation_artifacts=["pkg/interface/cli.py"],
                    suspected_scopes=["interface"],
                    recovery_action="implementation_repair",
                ),
            ]
        )

        self.assertEqual(route.action, RECOVER_IMPLEMENTATION)
        self.assertIn("implementation blocker", route.reason)

    def test_final_missing_test_recovery_enables_targeted_test_regeneration(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="domain", type="code_module", artifacts=["pkg/domain/resources.py"]),
                    WorkScope(id="core", type="code_module", artifacts=["pkg/core/engine.py"]),
                ],
                work_items=[
                    WorkItem(
                        id="implement:domain",
                        kind="coding",
                        scope_id="domain",
                        status="VERIFIED",
                        target_artifacts=["pkg/domain/resources.py"],
                        acceptance_criteria=["domain implementation exists"],
                    ),
                    WorkItem(
                        id="implement:core",
                        kind="coding",
                        scope_id="core",
                        status="VERIFIED",
                        target_artifacts=["pkg/core/engine.py"],
                        acceptance_criteria=["core implementation exists"],
                    ),
                ],
                team_gates=[
                    TeamGateSpec(scope_id="domain", test_artifacts=["tests/test_domain.py"]),
                    TeamGateSpec(scope_id="core", test_artifacts=["tests/test_engine.py"]),
                ],
                final_gate=FinalGateSpec(
                    required_artifacts=[
                        "pkg/domain/resources.py",
                        "pkg/core/engine.py",
                        "tests/test_domain.py",
                        "tests/test_engine.py",
                        "tests/test_integration.py",
                    ],
                    python_artifacts=[
                        "pkg/domain/resources.py",
                        "pkg/core/engine.py",
                        "tests/test_domain.py",
                        "tests/test_engine.py",
                        "tests/test_integration.py",
                    ],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            for gate in contract.team_gates:
                store.update_gate_status(run_id, f"team:{gate.scope_id}", "PASSED", evidence=["passed"])
            diagnostics = DiagnosticBuilder.from_final_gate_failure(
                errors=[
                    "Integration gate failed: target artifact(s) missing: tests/test_domain.py, tests/test_engine.py"
                ],
                required_artifacts=contract.final_gate.required_artifacts,
                artifact_scope_map=contract.owner_hints,
            )
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=[diagnostics[0].summary()],
                metadata={"diagnostics": [diagnostic.to_record() for diagnostic in diagnostics]},
            )
            store.update_run_status(run_id, "BLOCKED")
            engine = RunEngine(config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")), store=store)

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            self.assertEqual(store.get_work_item(run_id, "implement:domain").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:core").status, "VERIFIED")
            gate = store.get_gate(run_id, "final")
            self.assertEqual(gate.status, "PENDING")
            self.assertTrue(gate.metadata.get("allow_test_repair"))
            self.assertEqual(
                gate.metadata.get("target_test_artifacts"),
                ["tests/test_domain.py", "tests/test_engine.py"],
            )
            tickets = store.list_repair_tickets(run_id)
            self.assertEqual(tickets[0].lane, "test_regeneration")
            self.assertEqual(tickets[0].owner_scope, "tests")

    def test_final_test_regeneration_stops_after_repeated_same_diagnostic(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="core", type="code_module", artifacts=["pkg/core.py"])],
                work_items=[
                    WorkItem(
                        id="implement:core",
                        kind="coding",
                        scope_id="core",
                        status="VERIFIED",
                        target_artifacts=["pkg/core.py"],
                        acceptance_criteria=["core implementation exists"],
                    )
                ],
                team_gates=[TeamGateSpec(scope_id="core", test_artifacts=["tests/test_core.py"])],
                final_gate=FinalGateSpec(
                    required_artifacts=["pkg/core.py", "tests/test_core.py", "tests/test_integration.py"],
                    python_artifacts=["pkg/core.py", "tests/test_core.py", "tests/test_integration.py"],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            diagnostics = DiagnosticBuilder.from_final_gate_failure(
                errors=["Integration gate failed: target artifact(s) missing: tests/test_core.py"],
                required_artifacts=contract.final_gate.required_artifacts,
                artifact_scope_map=contract.owner_hints,
            )
            fingerprint = diagnostics[0].fingerprint()
            store.update_run_status(run_id, "BLOCKED", metadata={"diagnostic_fingerprints": {fingerprint: 2}})
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=[diagnostics[0].summary()],
                metadata={"diagnostics": [diagnostic.to_record() for diagnostic in diagnostics]},
            )
            engine = RunEngine(config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")), store=store)

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 1},
            )

            self.assertFalse(recovered)
            gate = store.get_gate(run_id, "final")
            self.assertEqual(gate.status, "BLOCKED")
            self.assertIn("blind test rewriting is disabled", "\n".join(gate.evidence))

    def test_final_test_generation_can_target_team_scope_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "domain"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "domain", "resources.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 7\n")
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="domain", type="code_module", artifacts=["pkg/domain/resources.py"])],
                work_items=[
                    WorkItem(
                        id="implement:domain",
                        kind="coding",
                        scope_id="domain",
                        status="VERIFIED",
                        target_artifacts=["pkg/domain/resources.py"],
                        acceptance_criteria=["domain implementation exists"],
                    )
                ],
                team_gates=[TeamGateSpec(scope_id="domain", test_artifacts=["tests/test_domain.py"])],
                final_gate=FinalGateSpec(
                    required_artifacts=[
                        "pkg/__init__.py",
                        "pkg/domain/resources.py",
                        "tests/test_domain.py",
                    ],
                    python_artifacts=[
                        "pkg/__init__.py",
                        "pkg/domain/resources.py",
                        "tests/test_domain.py",
                    ],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_gate_status(
                run_id,
                "final",
                "PENDING",
                metadata={"allow_test_repair": True, "target_test_artifacts": ["tests/test_domain.py"]},
            )
            run = store.get_run(run_id)

            def executor(item, agent_name, state):
                self.assertEqual(item.target_artifacts, ["tests/test_domain.py"])
                target = os.path.join(tmpdir, "tests", "test_domain.py")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8") as handle:
                    handle.write(
                        "import unittest\n"
                        "from pkg.domain import resources\n\n"
                        "class DomainTests(unittest.TestCase):\n"
                        "    def test_value(self):\n"
                        "        self.assertEqual(resources.VALUE, 7)\n"
                    )
                return {"output": "created tests/test_domain.py"}

            runner = GateRunner(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                team_runtime=TeamRuntime(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")), store),
                step_executor=executor,
            )

            result = runner._generate_final_gate_tests(run, contract)

            self.assertTrue(result.ok)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "tests", "test_domain.py")))

    def test_final_test_artifacts_include_scope_and_integration_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="domain", type="code_module", artifacts=["pkg/domain.py"])],
                work_items=[
                    WorkItem(
                        id="implement:domain",
                        kind="coding",
                        scope_id="domain",
                        target_artifacts=["pkg/domain.py"],
                    )
                ],
                team_gates=[TeamGateSpec(scope_id="domain", test_artifacts=["tests/test_domain.py"])],
                final_gate=FinalGateSpec(
                    required_artifacts=[
                        "pkg/domain.py",
                        "tests/test_domain.py",
                        "tests/test_integration.py",
                    ],
                    python_artifacts=[
                        "pkg/domain.py",
                        "tests/test_domain.py",
                        "tests/test_integration.py",
                    ],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            runner = GateRunner(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                team_runtime=TeamRuntime(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")), store),
            )

            self.assertEqual(
                runner._final_test_artifacts(contract),
                ["tests/test_domain.py", "tests/test_integration.py"],
            )

    def test_final_test_prompt_uses_target_heading_and_strict_test_skill_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[],
                work_items=[],
                final_gate=FinalGateSpec(
                    required_artifacts=["pkg/core.py", "tests/test_core.py"],
                    python_artifacts=["pkg/core.py", "tests/test_core.py"],
                    package_roots=["pkg"],
                    requires_tests=True,
                    product_behavior={
                        "capabilities": ["cli_blackbox", "failure_repair"],
                        "blackbox_commands": [
                            {"id": "pkg.cli:help", "argv": ["{python}", "-m", "pkg.cli", "--help"]}
                        ],
                        "semantic_requirements": [
                            {
                                "id": "failure_repair_is_semantic_not_note_text",
                                "description": "Failure repair must be asserted semantically.",
                            }
                        ],
                    },
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            runner = GateRunner(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                team_runtime=TeamRuntime(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")), store),
            )

            prompt = runner._final_test_generation_prompt(contract, ["tests/test_core.py"])

            self.assertIn("Target files in this module wave:", prompt)
            self.assertIn("create or update every file", prompt)
            self.assertIn("implementation_bug", prompt)
            self.assertIn("Product behavior contract:", prompt)
            self.assertIn("pkg.cli", prompt)
            self.assertIn("sys.executable -m", prompt)
            self.assertIn("failure_repair_is_semantic_not_note_text", prompt)

    def test_gate_diagnostic_separates_test_impl_and_external_artifacts(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_IMPLEMENTATION

        text = "\n".join(
            [
                "Unit test validation failed for tests/test_ai.py:",
                "Traceback (most recent call last):",
                '  File "/private/tmp/sandbox/nebula_colony/ai/heuristics.py", line 42, in rank_actions',
                "    json.loads('{}')",
                '  File "/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/json/decoder.py", line 1',
                "AssertionError: survival != science",
            ]
        )

        diagnostic = DiagnosticBuilder.from_gate_failure(
            gate_id="team:ai",
            scope_id="ai",
            errors=[text],
            affected_artifacts=["tests/test_ai.py"],
        )[0]
        route = FailureRouter().classify_diagnostics([diagnostic])

        self.assertIn("ai/heuristics.py", diagnostic.suspected_implementation_artifacts)
        self.assertIn("tests/test_ai.py", diagnostic.test_artifacts)
        self.assertNotIn("json/decoder.py", diagnostic.suspected_implementation_artifacts)
        self.assertEqual(route.action, RECOVER_IMPLEMENTATION)

    def test_gate_diagnostic_maps_package_init_to_package_scope(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        diagnostic = DiagnosticBuilder.from_gate_failure(
            gate_id="team:interface",
            scope_id="interface",
            errors=[
                "Unit test validation failed for tests/test_cli.py:\n"
                "  File \"/tmp/work/rulegrid/__init__.py\", line 19, in __getattr__\n"
                "ModuleNotFoundError: No module named 'rulegrid.core'"
            ],
            affected_artifacts=["tests/test_cli.py", "rulegrid/__init__.py"],
        )[0]

        self.assertIn("rulegrid/__init__.py", diagnostic.suspected_implementation_artifacts)
        self.assertEqual(diagnostic.suspected_scopes[0], "package")

    def test_unittest_failure_without_impl_suspect_routes_to_scope_implementation(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_IMPLEMENTATION

        diagnostic = DiagnosticBuilder.from_gate_failure(
            gate_id="team:ai",
            scope_id="ai",
            errors=["Unit test validation failed for tests/test_ai.py:\nFAIL: test_survival\nAssertionError: [] != ['survival']"],
            affected_artifacts=["tests/test_ai.py"],
        )[0]

        self.assertIn("ai/policies.py", diagnostic.suspected_implementation_artifacts)
        self.assertNotIn("tests/test_ai.py", diagnostic.suspected_implementation_artifacts)
        self.assertEqual(FailureRouter().classify_diagnostics([diagnostic]).action, RECOVER_IMPLEMENTATION)

    def test_final_gate_diagnostic_maps_traceback_to_implementation_scopes(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_IMPLEMENTATION

        required = [
            "nebula_colony/ai/planner.py",
            "nebula_colony/interface/cli.py",
            "tests/test_integration.py",
        ]
        artifact_scope_map = {
            "nebula_colony/ai/planner.py": "ai",
            "nebula_colony/interface/cli.py": "interface",
            "tests/test_integration.py": "integration",
        }
        text = "\n".join(
            [
                "Unittest discovery failed:",
                "ERROR: test_cli_public_entrypoints_smoke_with_real_scenarios (tests.test_integration.FinalIntegrationGateTests.test_cli_public_entrypoints_smoke_with_real_scenarios)",
                "Traceback (most recent call last):",
                '  File "/private/tmp/run/nebula_colony/interface/cli.py", line 179, in _plan_actions',
                "    planner = planner_cls(policy=policy)",
                "TypeError: ColonyPlanner.__init__() got an unexpected keyword argument 'policy'",
            ]
        )

        diagnostic = DiagnosticBuilder.from_final_gate_failure(
            errors=[text],
            required_artifacts=required,
            artifact_scope_map=artifact_scope_map,
        )[0]
        route = FailureRouter().classify_diagnostics([diagnostic])

        self.assertIn("nebula_colony/interface/cli.py", diagnostic.suspected_implementation_artifacts)
        self.assertTrue({"interface", "ai"}.issubset(set(diagnostic.suspected_scopes)))
        self.assertEqual(route.action, RECOVER_IMPLEMENTATION)

    def test_final_gate_cli_json_failure_routes_to_interface_owner_first(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        required = [
            "nebula_colony/interface/cli.py",
            "nebula_colony/io/scenarios.py",
            "nebula_colony/core/engine.py",
            "tests/test_integration.py",
        ]
        artifact_scope_map = {
            "nebula_colony/interface/cli.py": "interface",
            "nebula_colony/io/scenarios.py": "io",
            "nebula_colony/core/engine.py": "core",
            "tests/test_integration.py": "integration",
        }

        diagnostic = DiagnosticBuilder.from_final_gate_failure(
            errors=[
                "Unittest discovery failed:\n"
                "ERROR: test_cli_validate_outputs_json\n"
                "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
                "command: python -m nebula_colony.interface.cli validate --json\n"
                "stdout was empty"
            ],
            required_artifacts=required,
            artifact_scope_map=artifact_scope_map,
        )[0]

        self.assertEqual(diagnostic.primary_scope, "interface")
        self.assertEqual(diagnostic.suspected_scopes[0], "interface")
        self.assertIn("nebula_colony/interface/cli.py", diagnostic.suspected_implementation_artifacts)
        self.assertNotIn("core", diagnostic.fallback_scopes)

    def test_final_gate_package_export_failure_routes_to_package_owner(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        diagnostic = DiagnosticBuilder.from_final_gate_failure(
            errors=[
                "Unittest discovery failed:\n"
                "FAIL: test_package_exports\n"
                "AssertionError: ColonyPlanner not found in package __all__"
            ],
            required_artifacts=["nebula_colony/__init__.py", "nebula_colony/ai/planner.py", "tests/test_integration.py"],
            artifact_scope_map={
                "nebula_colony/__init__.py": "package",
                "nebula_colony/ai/planner.py": "ai",
                "tests/test_integration.py": "integration",
            },
        )[0]

        self.assertEqual(diagnostic.primary_scope, "package")
        self.assertIn("nebula_colony/__init__.py", diagnostic.suspected_implementation_artifacts)

    def test_final_gate_cli_constructor_failure_ignores_package_init_noise(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        required = [
            "atlas_workflow/__init__.py",
            "atlas_workflow/interface/cli.py",
            "atlas_workflow/domain/events.py",
            "tests/test_interface.py",
        ]
        diagnostic = DiagnosticBuilder.from_final_gate_failure(
            errors=[
                "Unittest discovery failed:\n"
                "ERROR: test_cli_smoke_commands_and_persisted_outputs\n"
                "Traceback (most recent call last):\n"
                '  File "/tmp/run/atlas_workflow/__init__.py", line 4, in <module>\n'
                "    from .interface.cli import main\n"
                '  File "/tmp/run/atlas_workflow/interface/cli.py", line 87, in persist_simulation\n'
                "    event = DomainEvent(event_type='simulation.persisted')\n"
                "TypeError: DomainEvent.__init__() missing 1 required positional argument: 'event_id'\n"
            ],
            required_artifacts=required,
            artifact_scope_map={
                "atlas_workflow/__init__.py": "package",
                "atlas_workflow/interface/cli.py": "interface",
                "atlas_workflow/domain/events.py": "domain",
                "tests/test_interface.py": "integration",
            },
        )[0]

        self.assertEqual(diagnostic.primary_scope, "interface")
        self.assertEqual(diagnostic.suspected_scopes[0], "interface")
        self.assertEqual(diagnostic.suspected_implementation_artifacts[0], "atlas_workflow/interface/cli.py")
        self.assertNotIn("atlas_workflow/__init__.py", diagnostic.suspected_implementation_artifacts)

    def test_final_gate_diagnostic_infers_scopes_from_integration_assertions(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder
        from ContractCoding.quality.failure_router import FailureRouter, RECOVER_IMPLEMENTATION

        required = [
            "nebula_colony/domain/colony.py",
            "nebula_colony/domain/population.py",
            "nebula_colony/core/engine.py",
            "nebula_colony/io/scenarios.py",
            "tests/test_integration.py",
        ]
        artifact_scope_map = {
            "nebula_colony/domain/colony.py": "domain",
            "nebula_colony/domain/population.py": "domain",
            "nebula_colony/core/engine.py": "core",
            "nebula_colony/io/scenarios.py": "io",
            "tests/test_integration.py": "integration",
        }

        diagnostic = DiagnosticBuilder.from_final_gate_failure(
            errors=[
                "Unittest discovery failed:\n"
                "FAIL: test_complete_thirty_turn_first_light_scenario_is_deterministic_and_persistent\n"
                "AssertionError: 10 != 30\n"
                "FAIL: test_domain_colony_can_feed_engine_and_remain_serializable\n"
                "AssertionError: 0 != 14"
            ],
            required_artifacts=required,
            artifact_scope_map=artifact_scope_map,
        )[0]

        self.assertTrue({"core", "io", "domain"}.issubset(set(diagnostic.suspected_scopes)))
        self.assertIn("nebula_colony/core/engine.py", diagnostic.suspected_implementation_artifacts)
        self.assertEqual(FailureRouter().classify_diagnostics([diagnostic]).action, RECOVER_IMPLEMENTATION)

    def test_final_gate_repair_creates_centralized_primary_bundle(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder

        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="interface", type="code_module", artifacts=["pkg/interface/cli.py"]),
                    WorkScope(id="io", type="code_module", artifacts=["pkg/io/scenarios.py"]),
                    WorkScope(id="core", type="code_module", artifacts=["pkg/core/engine.py"]),
                ],
                work_items=[
                    WorkItem(id="implement:interface:cli", kind="coding", scope_id="interface", status="VERIFIED", target_artifacts=["pkg/interface/cli.py"], acceptance_criteria=["CLI emits JSON"]),
                    WorkItem(id="implement:io:scenarios", kind="coding", scope_id="io", status="VERIFIED", target_artifacts=["pkg/io/scenarios.py"], acceptance_criteria=["scenarios load"]),
                    WorkItem(id="implement:core:engine", kind="coding", scope_id="core", status="VERIFIED", target_artifacts=["pkg/core/engine.py"], acceptance_criteria=["engine runs"]),
                ],
                team_gates=[
                    TeamGateSpec(scope_id="interface"),
                    TeamGateSpec(scope_id="io"),
                    TeamGateSpec(scope_id="core"),
                ],
                final_gate=FinalGateSpec(
                    required_artifacts=[
                        "pkg/interface/cli.py",
                        "pkg/io/scenarios.py",
                        "pkg/core/engine.py",
                        "tests/test_integration.py",
                    ],
                    python_artifacts=[
                        "pkg/interface/cli.py",
                        "pkg/io/scenarios.py",
                        "pkg/core/engine.py",
                        "tests/test_integration.py",
                    ],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            diagnostics = DiagnosticBuilder.from_final_gate_failure(
                errors=[
                    "Unittest discovery failed:\n"
                    "ERROR: test_cli_validate_outputs_json\n"
                    "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
                    "command: python -m pkg.interface.cli validate --json\n"
                    "stdout was empty"
                ],
                required_artifacts=contract.final_gate.required_artifacts,
                artifact_scope_map=contract.owner_hints,
            )
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=[diagnostics[0].summary()],
                metadata={"diagnostics": [diagnostic.to_record() for diagnostic in diagnostics]},
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 2},
            )

            self.assertTrue(recovered)
            self.assertEqual(store.get_work_item(run_id, "implement:interface:cli").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:io:scenarios").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:core:engine").status, "VERIFIED")
            final_repair = store.get_work_item(run_id, "final_repair:convergence")
            self.assertIsNotNone(final_repair)
            self.assertEqual(final_repair.status, "READY")
            self.assertEqual(final_repair.owner_profile, "Recovery_Orchestrator")
            self.assertIn("pkg/interface/cli.py", final_repair.target_artifacts)
            ticket = next(ticket for ticket in store.list_repair_tickets(run_id) if ticket.owner_scope == "final_repair")
            self.assertEqual(ticket.owner_scope, "final_repair")
            self.assertIn("centralized_final_repair", ticket.metadata)

    def test_final_gate_multi_owner_failure_opens_centralized_repair_bundle(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="interface", type="code_module", artifacts=["pkg/interface/cli.py"]),
                    WorkScope(id="core", type="code_module", artifacts=["pkg/core/runtime.py"]),
                ],
                work_items=[
                    WorkItem(id="implement:interface:cli", kind="coding", scope_id="interface", status="VERIFIED", target_artifacts=["pkg/interface/cli.py"], acceptance_criteria=["CLI emits JSON"]),
                    WorkItem(id="implement:core:runtime", kind="coding", scope_id="core", status="VERIFIED", target_artifacts=["pkg/core/runtime.py"], acceptance_criteria=["runtime completes workflows"]),
                ],
                team_gates=[TeamGateSpec(scope_id="interface"), TeamGateSpec(scope_id="core")],
                final_gate=FinalGateSpec(
                    required_artifacts=["pkg/interface/cli.py", "pkg/core/runtime.py", "tests/test_integration.py"],
                    python_artifacts=["pkg/interface/cli.py", "pkg/core/runtime.py", "tests/test_integration.py"],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            for gate_id in ("team:interface", "team:core"):
                store.update_gate_status(run_id, gate_id, "PASSED", evidence=["passed"])
            diagnostic = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                failing_test="tests.test_integration.FinalTests.test_cli_and_workflow",
                suspected_implementation_artifacts=["pkg/interface/cli.py", "pkg/core/runtime.py"],
                suspected_scopes=["interface", "core"],
                primary_scope="interface",
                fallback_scopes=["core"],
                test_artifacts=["tests/test_integration.py"],
            )
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=[diagnostic.summary()],
                metadata={"diagnostics": [diagnostic.to_record()]},
            )
            store.update_run_status(run_id, "BLOCKED")
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 3, "test_repair_limit": 5, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            self.assertEqual(store.get_work_item(run_id, "implement:interface:cli").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:core:runtime").status, "VERIFIED")
            final_repair = store.get_work_item(run_id, "final_repair:convergence")
            self.assertIsNotNone(final_repair)
            self.assertEqual(final_repair.status, "READY")
            self.assertEqual(
                set(final_repair.target_artifacts),
                {"pkg/interface/cli.py", "pkg/core/runtime.py"},
            )
            ticket = next(ticket for ticket in store.list_repair_tickets(run_id) if ticket.owner_scope == "final_repair")
            bundle = ticket.metadata["repair_bundle"]
            self.assertEqual(ticket.owner_scope, "final_repair")
            self.assertEqual(bundle["strategy"], "centralized")
            self.assertEqual(bundle["centralized_work_item"], "final_repair:convergence")
            self.assertEqual(set(bundle["owner_artifacts"]), {"pkg/interface/cli.py", "pkg/core/runtime.py"})
            graph = engine.graph(run_id)
            self.assertEqual(graph["repair_tickets"][0]["repair_bundle"]["strategy"], "centralized")

    def test_final_repair_out_of_scope_blocker_widens_central_bundle(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            diagnostic = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_failure",
                failing_test="tests.test_interface.test_cli_smoke_commands_and_persisted_outputs",
                suspected_implementation_artifacts=[
                    "pkg/interface/cli.py",
                    "pkg/domain/events.py",
                ],
                suspected_scopes=["interface", "domain"],
                primary_scope="interface",
                test_artifacts=["tests/test_interface.py"],
                repair_instruction="Repair final integration failure in interface.",
            )
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="package", type="code_module", artifacts=["pkg/__init__.py"]),
                    WorkScope(id="interface", type="code_module", artifacts=["pkg/interface/cli.py"]),
                    WorkScope(id="domain", type="code_module", artifacts=["pkg/domain/events.py"]),
                ],
                work_items=[
                    WorkItem(
                        id="implement:package",
                        kind="coding",
                        scope_id="package",
                        status="VERIFIED",
                        target_artifacts=["pkg/__init__.py"],
                        acceptance_criteria=["package exports stay importable"],
                    ),
                    WorkItem(
                        id="implement:interface:cli",
                        kind="coding",
                        scope_id="interface",
                        status="VERIFIED",
                        target_artifacts=["pkg/interface/cli.py"],
                        acceptance_criteria=["CLI persists simulations"],
                    ),
                    WorkItem(
                        id="implement:domain:events",
                        kind="coding",
                        scope_id="domain",
                        status="VERIFIED",
                        target_artifacts=["pkg/domain/events.py"],
                        acceptance_criteria=["Domain events expose required fields"],
                    ),
                    WorkItem(
                        id="final_repair:convergence",
                        kind="coding",
                        owner_profile="Recovery_Orchestrator",
                        scope_id="final_repair",
                        status="BLOCKED",
                        target_artifacts=["pkg/__init__.py"],
                        acceptance_criteria=["centralized final repair"],
                        inputs={
                            "final_repair_mode": "centralized_convergence",
                            "repair_ticket_id": "ticket-final",
                            "diagnostics": [diagnostic.to_record()],
                            "repair_instruction": "Repair final integration failure centrally.",
                            "repair_bundle": {
                                "strategy": "centralized",
                                "owner_artifacts": ["pkg/__init__.py"],
                            },
                        },
                    ),
                ],
                team_gates=[
                    TeamGateSpec(scope_id="package"),
                    TeamGateSpec(scope_id="interface"),
                    TeamGateSpec(scope_id="domain"),
                ],
                final_gate=FinalGateSpec(
                    required_artifacts=[
                        "pkg/__init__.py",
                        "pkg/interface/cli.py",
                        "pkg/domain/events.py",
                        "tests/test_interface.py",
                    ],
                    python_artifacts=[
                        "pkg/__init__.py",
                        "pkg/interface/cli.py",
                        "pkg/domain/events.py",
                        "tests/test_interface.py",
                    ],
                    package_roots=["pkg"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            step_id = store.create_step(run_id, "final_repair:convergence", "Recovery_Orchestrator")
            store.finish_step(
                step_id,
                "ERROR",
                error=(
                    'Agent reported a blocker: {"blocker_type":"out_of_scope_repair",'
                    '"required_artifacts":["pkg/interface/cli.py"],'
                    '"current_allowed_artifacts":["pkg/__init__.py"],'
                    '"reason":"CLI owns the failing constructor call"}'
                ),
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 2, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            final_repair = store.get_work_item(run_id, "final_repair:convergence")
            self.assertEqual(final_repair.status, "READY")
            self.assertEqual(
                set(final_repair.target_artifacts),
                {"pkg/__init__.py", "pkg/interface/cli.py"},
            )
            self.assertEqual(store.get_work_item(run_id, "implement:package").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:interface:cli").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:domain:events").status, "VERIFIED")
            events = store.list_events(run_id)
            self.assertTrue(any(event.event_type == "final_repair_bundle_widened" for event in events))
            self.assertFalse(any(event.event_type == "final_repair_rerouted" for event in events))

    def test_gate_repair_targets_suspected_artifact_batch_only(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=RunStore.for_workspace(tmpdir),
                step_executor=lambda *_: {"output": "noop"},
            )
            items = [
                WorkItem(id="implement:core:engine", kind="coding", scope_id="core", target_artifacts=["pkg/core/engine.py"]),
                WorkItem(
                    id="implement:core:systems",
                    kind="coding",
                    scope_id="core",
                    target_artifacts=["pkg/core/economy.py", "pkg/core/disasters.py"],
                ),
            ]
            diagnostic = DiagnosticRecord(
                gate_id="team:core",
                scope_id="core",
                failure_kind="unittest_assertion",
                suspected_implementation_artifacts=["core/disasters.py"],
                test_artifacts=["tests/test_engine.py"],
            )

            matched = engine.auto_steward.recovery._items_for_diagnostics(items, "core", [diagnostic])

            self.assertEqual([item.id for item in matched], ["implement:core:systems"])

    def test_final_gate_repair_targets_suspected_scopes_and_not_tests(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=RunStore.for_workspace(tmpdir),
                step_executor=lambda *_: {"output": "noop"},
            )
            items = [
                WorkItem(id="implement:domain", kind="coding", scope_id="domain", target_artifacts=["pkg/domain/colony.py"]),
                WorkItem(id="implement:core:engine", kind="coding", scope_id="core", target_artifacts=["pkg/core/engine.py"]),
                WorkItem(id="implement:io:scenarios", kind="coding", scope_id="io", target_artifacts=["pkg/io/scenarios.py"]),
                WorkItem(id="gate:final:tests", kind="coding", scope_id="integration", target_artifacts=["tests/test_integration.py"]),
            ]
            diagnostic = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                suspected_scopes=["domain", "core"],
                test_artifacts=["tests/test_integration.py"],
            )

            matched = engine.auto_steward.recovery._items_for_diagnostics(items, "integration", [diagnostic])

        self.assertEqual([item.id for item in matched], ["implement:domain", "implement:core:engine"])

    def test_final_gate_repair_caps_broad_failure_clusters(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=RunStore.for_workspace(tmpdir),
                step_executor=lambda *_: {"output": "noop"},
            )
            items = [
                WorkItem(id="scaffold:domain", kind="doc", scope_id="domain", target_artifacts=["pkg/domain/state.py"]),
                WorkItem(id="implement:domain:assets", kind="coding", scope_id="domain", target_artifacts=["pkg/domain/assets.py"]),
                WorkItem(id="implement:domain:orders", kind="coding", scope_id="domain", target_artifacts=["pkg/domain/orders.py"]),
                WorkItem(id="implement:core:dispatch", kind="coding", scope_id="core", target_artifacts=["pkg/core/dispatch.py"]),
                WorkItem(id="implement:core:routing", kind="coding", scope_id="core", target_artifacts=["pkg/core/routing.py"]),
                WorkItem(id="implement:planning", kind="coding", scope_id="planning", target_artifacts=["pkg/planning/planner.py"]),
                WorkItem(id="implement:io:scenarios", kind="coding", scope_id="io", target_artifacts=["pkg/io/scenarios.py"]),
                WorkItem(id="implement:interface:cli", kind="coding", scope_id="interface", target_artifacts=["pkg/interface/cli.py"]),
                WorkItem(id="gate:final:tests", kind="coding", scope_id="integration", target_artifacts=["tests/test_integration.py"]),
            ]
            diagnostic = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                failing_test="test_cli_plan_uses_requested_scenario",
                suspected_implementation_artifacts=[
                    "pkg/interface/cli.py",
                    "pkg/io/scenarios.py",
                    "pkg/domain/assets.py",
                    "pkg/core/dispatch.py",
                    "pkg/planning/planner.py",
                    "pkg/core/routing.py",
                ],
                suspected_scopes=["interface", "io", "domain", "core", "planning"],
                primary_scope="interface",
                test_artifacts=["tests/test_integration.py"],
            )

            matched = engine.auto_steward.recovery._items_for_diagnostics(
                items,
                "integration",
                [diagnostic],
                gate_id="final",
                route_action="implementation_repair",
            )

        self.assertLessEqual(len(matched), 5)
        self.assertNotIn("scaffold:domain", [item.id for item in matched])
        self.assertNotIn("gate:final:tests", [item.id for item in matched])
        self.assertEqual(matched[0].id, "implement:interface:cli")

    def test_final_gate_recovery_reopens_implementation_and_scope_gate(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build a Python package with pkg/domain/colony.py pkg/core/engine.py tests/test_integration.py"
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                step_executor=lambda *_: {"output": "noop"},
            )
            engine.team_runtime.ensure_teams(run_id, contract)
            for item in store.list_work_items(run_id):
                payload = item.to_record()
                payload["status"] = "VERIFIED"
                store.upsert_work_item(run_id, WorkItem.from_mapping(payload))
            for gate in contract.team_gates:
                store.update_gate_status(run_id, f"team:{gate.scope_id}", "PASSED", evidence=["passed"])
            diagnostic = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                failing_test="test_domain_colony_can_feed_engine_and_remain_serializable",
                expected_actual="0 != 14",
                suspected_scopes=["domain", "core"],
                test_artifacts=["tests/test_integration.py"],
            )
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=["Unittest discovery failed: AssertionError: 0 != 14"],
                metadata={"diagnostics": [diagnostic.to_record()]},
            )
            store.update_run_status(run_id, "BLOCKED")

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 3, "test_repair_limit": 5, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            final_repair = store.get_work_item(run_id, "final_repair:convergence")
            self.assertIsNotNone(final_repair)
            self.assertEqual(final_repair.status, "READY")
            self.assertEqual(final_repair.scope_id, "final_repair")
            self.assertTrue(
                set(final_repair.target_artifacts).intersection(
                    {"pkg/domain/colony.py", "pkg/core/engine.py"}
                )
            )
            self.assertEqual(store.get_gate(run_id, "final").status, "PENDING")
            self.assertEqual(store.get_gate(run_id, "team:domain").status, "PASSED")
            self.assertEqual(store.get_gate(run_id, "team:core").status, "PASSED")
            event = next(event for event in store.list_events(run_id) if event.event_type == "gate_repair_requested")
            self.assertEqual(event.payload["route"]["action"], "implementation_repair")
            self.assertTrue(event.payload["centralized_final_repair"])
            self.assertEqual(event.payload["repair_plan"]["reopened_items"], ["final_repair:convergence"])

    def test_gate_repair_counter_is_fingerprint_scoped(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        one = DiagnosticRecord(
            gate_id="team:core",
            scope_id="core",
            failure_kind="unittest_assertion",
            failing_test="test_stage_signature",
            suspected_implementation_artifacts=["pkg/core/engine.py"],
        )
        two = DiagnosticRecord(
            gate_id="team:core",
            scope_id="core",
            failure_kind="unittest_assertion",
            failing_test="test_frozen_state_replace",
            suspected_implementation_artifacts=["pkg/core/engine.py"],
            suspected_symbols=["replace"],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            steward = RunEngine(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))).auto_steward.recovery
            key_one = steward._gate_repair_counter_id("team:core", "implementation_repair", [one])
            key_two = steward._gate_repair_counter_id("team:core", "implementation_repair", [two])

        self.assertNotEqual(key_one, key_two)
        self.assertIn(one.fingerprint(), key_one)
        self.assertIn(two.fingerprint(), key_two)

    def test_item_repair_creates_runtime_ticket_and_injects_ticket_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            store = RunStore.for_workspace(tmpdir)
            contract = ContractCompiler().compile("Implement broken.py")
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            store.update_work_item_status(
                run_id,
                "coding:broken.py",
                "BLOCKED",
                evidence=["Import validation failed for broken.py: NameError: name 'missing_name' is not defined"],
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 0},
            )

            self.assertTrue(recovered)
            tickets = store.list_repair_tickets(run_id)
            self.assertEqual(len(tickets), 1)
            self.assertEqual(tickets[0].lane, "local_patch")
            self.assertEqual(tickets[0].status, "RUNNING")
            self.assertEqual(tickets[0].source_item_id, "coding:broken.py")
            self.assertEqual(tickets[0].attempt_count, 1)
            item = store.get_work_item(run_id, "coding:broken.py")
            self.assertEqual(item.inputs["repair_ticket_id"], tickets[0].id)
            self.assertEqual(item.inputs["repair_packet"]["repair_ticket_id"], tickets[0].id)
            self.assertEqual(item.inputs["repair_packet"]["allowed_artifacts"], ["broken.py"])

    def test_repair_ticket_resolves_when_source_item_is_verified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            item = WorkItem(
                id="coding:pkg/mod.py",
                kind="coding",
                scope_id="pkg",
                target_artifacts=["pkg/mod.py"],
                status="READY",
            )
            run_id = store.create_run("demo", tmpdir, work_items=[item])
            ticket = store.ensure_repair_ticket(
                run_id=run_id,
                lane="local",
                source_item_id=item.id,
                diagnostic_fingerprint="fp",
                owner_scope="pkg",
                owner_artifacts=["pkg/mod.py"],
                conflict_keys=["artifact:pkg/mod.py"],
                failure_summary="syntax failure",
                repair_instruction="fix syntax",
            )
            store.increment_repair_ticket_attempt(ticket.id)
            store.update_work_item_status(run_id, item.id, "DONE", evidence=["done"])
            store.update_work_item_status(run_id, item.id, "VERIFIED", evidence=["verified"])
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                step_executor=lambda *_: {"output": "noop"},
            )

            engine._resolve_repair_tickets(run_id)

            self.assertEqual(store.get_repair_ticket(ticket.id).status, "RESOLVED")

    def test_final_gate_repair_creates_convergence_ticket(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="ai", type="code_module", artifacts=["pkg/ai/planner.py"]),
                    WorkScope(id="core", type="code_module", artifacts=["pkg/core/engine.py"]),
                ],
                work_items=[
                    WorkItem(id="implement:ai", kind="coding", scope_id="ai", status="VERIFIED", target_artifacts=["pkg/ai/planner.py"], acceptance_criteria=["planner works"]),
                    WorkItem(id="implement:core", kind="coding", scope_id="core", status="VERIFIED", target_artifacts=["pkg/core/engine.py"], acceptance_criteria=["engine works"]),
                ],
                team_gates=[TeamGateSpec(scope_id="ai"), TeamGateSpec(scope_id="core")],
                final_gate=FinalGateSpec(
                    required_artifacts=["pkg/ai/planner.py", "pkg/core/engine.py", "tests/test_integration.py"],
                    python_artifacts=["pkg/ai/planner.py", "pkg/core/engine.py", "tests/test_integration.py"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            diagnostic = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                failing_test="test_ai_planning_operates_on_real_engine",
                expected_actual="'gather_ore' != 'repair_convoy'",
                primary_scope="ai",
                suspected_scopes=["ai", "core"],
                suspected_implementation_artifacts=["pkg/ai/planner.py"],
                test_artifacts=["tests/test_integration.py"],
                recovery_action="implementation_repair",
            )
            store.update_gate_status(
                run_id,
                "final",
                "FAILED",
                evidence=[diagnostic.summary()],
                metadata={"diagnostics": [diagnostic.to_record()]},
            )
            store.update_run_status(run_id, "BLOCKED")
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                step_executor=lambda *_: {"output": "noop"},
            )

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 3, "test_repair_limit": 4, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            tickets = store.list_repair_tickets(run_id)
            self.assertTrue(any(ticket.lane == "integration_convergence" for ticket in tickets))
            ticket = next(ticket for ticket in tickets if ticket.owner_scope == "final_repair")
            self.assertEqual(ticket.owner_scope, "final_repair")
            self.assertIn("pkg/ai/planner.py", ticket.owner_artifacts)
            self.assertIn("pkg/core/engine.py", ticket.owner_artifacts)
            self.assertEqual(store.get_work_item(run_id, "implement:ai").status, "VERIFIED")
            self.assertEqual(store.get_work_item(run_id, "implement:core").status, "VERIFIED")
            final_repair = store.get_work_item(run_id, "final_repair:convergence")
            self.assertEqual(final_repair.status, "READY")
            self.assertEqual(final_repair.inputs["repair_packet"]["owner_scope"], "final_repair")
            event = next(event for event in store.list_events(run_id) if event.event_type == "gate_repair_requested")
            self.assertEqual(event.payload["recovery_decision"]["owner_scope"], "ai")
            self.assertEqual(set(event.payload["repair_plan"]["owner_artifacts"]), {"pkg/ai/planner.py", "pkg/core/engine.py"})
            self.assertEqual(event.payload["repair_plan"]["owner_scope"], "final_repair")
            self.assertEqual(event.payload["repair_plan"]["reopened_items"], ["final_repair:convergence"])
            self.assertEqual(event.payload["repair_plan"]["repair_packet"]["repair_ticket_id"], ticket.id)
            self.assertEqual(
                final_repair.inputs["repair_packet"]["locked_artifacts"],
                ["tests/test_integration.py"],
            )

    def test_new_final_convergence_ticket_supersedes_prior_running_ticket(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="interface", type="code_module", artifacts=["pkg/interface.py"]),
                    WorkScope(id="core", type="code_module", artifacts=["pkg/core.py"]),
                ],
                work_items=[
                    WorkItem(id="implement:interface", kind="coding", scope_id="interface", status="VERIFIED", target_artifacts=["pkg/interface.py"], acceptance_criteria=["interface works"]),
                    WorkItem(id="implement:core", kind="coding", scope_id="core", status="VERIFIED", target_artifacts=["pkg/core.py"], acceptance_criteria=["core works"]),
                ],
                team_gates=[TeamGateSpec(scope_id="interface"), TeamGateSpec(scope_id="core")],
                final_gate=FinalGateSpec(
                    required_artifacts=["pkg/interface.py", "pkg/core.py", "tests/test_integration.py"],
                    python_artifacts=["pkg/interface.py", "pkg/core.py", "tests/test_integration.py"],
                    requires_tests=True,
                ),
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                step_executor=lambda *_: {"output": "noop"},
            )

            first = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                failing_test="test_cli",
                expected_actual="1 != 2",
                primary_scope="interface",
                suspected_scopes=["interface"],
                suspected_implementation_artifacts=["pkg/interface.py"],
                test_artifacts=["tests/test_integration.py"],
                recovery_action="implementation_repair",
            )
            store.update_gate_status(run_id, "final", "FAILED", evidence=[first.summary()], metadata={"diagnostics": [first.to_record()]})
            store.update_run_status(run_id, "BLOCKED")
            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 3, "test_repair_limit": 4, "contract_replan_limit": 1},
            )
            self.assertTrue(recovered)
            first_ticket = next(ticket for ticket in store.list_repair_tickets(run_id) if ticket.owner_scope == "final_repair")

            second = DiagnosticRecord(
                gate_id="final",
                scope_id="integration",
                failure_kind="unittest_assertion",
                failing_test="test_core",
                expected_actual="3 != 4",
                primary_scope="core",
                suspected_scopes=["core"],
                suspected_implementation_artifacts=["pkg/core.py"],
                test_artifacts=["tests/test_integration.py"],
                recovery_action="implementation_repair",
            )
            store.update_gate_status(run_id, "final", "FAILED", evidence=[second.summary()], metadata={"diagnostics": [second.to_record()]})
            store.update_run_status(run_id, "BLOCKED")
            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 3, "test_repair_limit": 4, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            tickets = [ticket for ticket in store.list_repair_tickets(run_id) if ticket.owner_scope == "final_repair"]
            latest = next(ticket for ticket in tickets if ticket.id != first_ticket.id)
            self.assertEqual(store.get_repair_ticket(first_ticket.id).status, "SUPERSEDED")
            self.assertEqual(store.get_repair_ticket(first_ticket.id).metadata["superseded_by"], latest.id)

    def test_interface_diagnostic_creates_impact_replan_ticket_without_reopening_items(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="interface", type="code_module", artifacts=["pkg/interface/cli.py"])],
                work_items=[
                    WorkItem(id="implement:interface", kind="coding", scope_id="interface", status="VERIFIED", target_artifacts=["pkg/interface/cli.py"], acceptance_criteria=["CLI works"]),
                ],
                team_gates=[TeamGateSpec(scope_id="interface")],
                acceptance_criteria=["done"],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            diagnostic = DiagnosticRecord(
                gate_id="team:interface",
                scope_id="interface",
                failure_kind="missing_or_ambiguous_interface",
                failing_test="test_cli_contract",
                expected_actual="missing CLI JSON schema",
                suspected_scopes=["interface"],
                recovery_action="interface_replan",
                repair_instruction="Clarify the CLI JSON response contract.",
            )
            store.update_gate_status(
                run_id,
                "team:interface",
                "FAILED",
                evidence=[diagnostic.summary()],
                metadata={"diagnostics": [diagnostic.to_record()]},
            )
            store.update_run_status(run_id, "BLOCKED")
            engine = RunEngine(
                config=Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log")),
                store=store,
                step_executor=lambda *_: {"output": "noop"},
            )

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 1},
            )

            self.assertFalse(recovered)
            ticket = store.list_repair_tickets(run_id)[0]
            self.assertEqual(ticket.lane, "interface_delta")
            self.assertEqual(ticket.status, "OPEN")
            self.assertEqual(store.get_work_item(run_id, "implement:interface").status, "VERIFIED")

    def test_ready_repair_tickets_are_conflict_aware_not_globally_serial(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir)
            first = store.ensure_repair_ticket(
                run_id=run_id,
                lane="local",
                source_item_id="a",
                diagnostic_fingerprint="a",
                owner_scope="core",
                owner_artifacts=["pkg/core/engine.py"],
                conflict_keys=["artifact:pkg/core/engine.py"],
                failure_summary="core failed",
                repair_instruction="fix core",
            )
            second = store.ensure_repair_ticket(
                run_id=run_id,
                lane="local",
                source_item_id="b",
                diagnostic_fingerprint="b",
                owner_scope="ai",
                owner_artifacts=["pkg/ai/planner.py"],
                conflict_keys=["artifact:pkg/ai/planner.py"],
                failure_summary="ai failed",
                repair_instruction="fix ai",
            )
            store.ensure_repair_ticket(
                run_id=run_id,
                lane="local",
                source_item_id="c",
                diagnostic_fingerprint="c",
                owner_scope="core",
                owner_artifacts=["pkg/core/engine.py"],
                conflict_keys=["artifact:pkg/core/engine.py"],
                failure_summary="same file failed",
                repair_instruction="fix same file",
            )

            ready = store.ready_repair_tickets(run_id)

            self.assertEqual({ticket.id for ticket in ready}, {first.id, second.id})

    def test_owner_resolver_routes_tick_diagnostic_to_ticks_artifact(self):
        from ContractCoding.quality.diagnostics import DiagnosticBuilder
        from ContractCoding.quality.owner import OwnerResolver

        diagnostic = DiagnosticBuilder.from_gate_failure(
            gate_id="team:core",
            scope_id="core",
            errors=[
                "Unit test validation failed for tests/test_engine.py:\n"
                "FAIL: test_ticks_between_uses_turn_clock\n"
                "Traceback (most recent call last):\n"
                "  File \"/tmp/run/tests/test_engine.py\", line 12, in test_ticks_between_uses_turn_clock\n"
                "    self.assertEqual(ticks_between(Tick(1), Tick(4)), 3)\n"
                "AssertionError: 2 != 3"
            ],
            affected_artifacts=["tests/test_engine.py"],
        )[0]
        resolution = OwnerResolver().resolve(
            diagnostic,
            candidate_artifacts=[
                "nebula_colony/core/engine.py",
                "nebula_colony/core/ticks.py",
                "tests/test_engine.py",
            ],
        )

        self.assertEqual(resolution.primary_artifact, "nebula_colony/core/ticks.py")
        self.assertNotEqual(resolution.primary_artifact, "nebula_colony/core/engine.py")

    def test_patch_guard_rolls_back_syntax_breaking_repair_write(self):
        from ContractCoding.llm.base import ToolIntent
        from ContractCoding.runtime.repair import PatchGuard
        from ContractCoding.tools.intent_executor import execute_tool_intents

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            target = os.path.join(tmpdir, "pkg", "mod.py")
            original = "def value():\n    return 1\n"
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(original)
            tools = build_file_tools(tmpdir)
            governor = ToolGovernor(approval_mode="auto-edit", allowed_artifacts=["pkg/mod.py"])
            intent = ToolIntent(
                name="update_file_lines",
                arguments={
                    "file_path": "pkg/mod.py",
                    "start_line": 1,
                    "end_line": 2,
                    "new_content": "def value():\nreturn 2\n",
                },
                reason="simulate bad repair",
            )

            results = execute_tool_intents(
                [intent],
                tools,
                governor,
                tool_execution_observer=PatchGuard(
                    tmpdir,
                    allowed_artifacts=["pkg/mod.py"],
                    diagnostic_text="Repair diagnostics:\n- kind: syntax_error\n",
                ),
            )

            with open(target, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)
            self.assertTrue(results[0].rolled_back)
            self.assertEqual(results[0].validation_status, "rolled_back")
            self.assertIn("contractcoding_repair_validation", results[0].output)

    def test_patch_guard_keeps_local_patch_when_broad_final_still_fails_elsewhere(self):
        from ContractCoding.llm.base import ToolIntent
        from ContractCoding.runtime.repair import PatchGuard
        from ContractCoding.tools.intent_executor import execute_tool_intents

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            target = os.path.join(tmpdir, "pkg", "mod.py")
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("def value():\n    return 1\n")
            with open(os.path.join(tmpdir, "tests", "test_other.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import unittest\n\n"
                    "class OtherFailure(unittest.TestCase):\n"
                    "    def test_elsewhere(self):\n"
                    "        self.assertEqual(1, 2)\n"
                )
            tools = build_file_tools(tmpdir)
            governor = ToolGovernor(approval_mode="auto-edit", allowed_artifacts=["pkg/mod.py"])
            intent = ToolIntent(
                name="update_file_lines",
                arguments={
                    "file_path": "pkg/mod.py",
                    "start_line": 2,
                    "end_line": 2,
                    "new_content": "    return 2\n",
                },
                reason="simulate local final repair",
            )

            results = execute_tool_intents(
                [intent],
                tools,
                governor,
                tool_execution_observer=PatchGuard(
                    tmpdir,
                    allowed_artifacts=["pkg/mod.py"],
                    diagnostic_text="Repair diagnostics:\n- gate_id: final\n- kind: unittest_assertion\nfinal integration failure",
                ),
            )

            with open(target, "r", encoding="utf-8") as handle:
                self.assertIn("return 2", handle.read())
            self.assertFalse(results[0].rolled_back)
            self.assertEqual(results[0].validation_status, "applied_pending_global_validation")
            self.assertIn("bundle-level convergence", results[0].output)

    def test_item_repair_counter_is_fingerprint_and_owner_scoped(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            steward = RunEngine(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))).auto_steward.recovery
            item = WorkItem(
                id="implement:core:clock",
                kind="coding",
                scope_id="core",
                target_artifacts=["pkg/core/ticks.py"],
            )
            first = DiagnosticRecord(
                gate_id="item:implement:core:clock",
                scope_id="core",
                failure_kind="syntax_error",
                traceback_excerpt="SyntaxError: invalid syntax at Tick line 20",
                suspected_implementation_artifacts=["pkg/core/ticks.py"],
            )
            second = DiagnosticRecord(
                gate_id="item:implement:core:clock",
                scope_id="core",
                failure_kind="syntax_error",
                traceback_excerpt="SyntaxError: invalid syntax at TurnClock line 40",
                suspected_implementation_artifacts=["pkg/core/ticks.py"],
                suspected_symbols=["TurnClock"],
            )

            key_one = steward._item_repair_counter_id(item, "item_quality", [first])
            key_two = steward._item_repair_counter_id(item, "item_quality", [second])

        self.assertNotEqual(key_one, key_two)
        self.assertIn("pkg/core/ticks.py", key_one)
        self.assertIn(first.fingerprint(), key_one)
        self.assertIn(second.fingerprint(), key_two)

    def test_agent_blocker_output_is_treated_as_validation_failure(self):
        blocker = TeamExecutor._payload_agent_blocker(
            {
                "output": (
                    "Blocker: the required fix is in nebula_colony/core/disasters.py, "
                    "which is outside this WorkItem target set."
                )
            }
        )

        self.assertIn("Agent reported a blocker", blocker)

    def test_completed_output_that_mentions_no_blocker_is_not_a_blocker(self):
        blocker = TeamExecutor._payload_agent_blocker(
            {
                "output": (
                    "Completed the in-scope repair for `nebula_colony/io/scenarios.py`.\n"
                    "Changed files: nebula_colony/io/scenarios.py.\n"
                    "No blocker remains; no shell verification was requested."
                )
            }
        )

        self.assertEqual(blocker, "")

    def test_structured_blocker_json_is_treated_as_validation_failure(self):
        blocker = TeamExecutor._payload_agent_blocker(
            {
                "output": (
                    '{"blocker_type":"out_of_scope_repair",'
                    '"required_artifacts":["nebula_colony/io/save_load.py"],'
                    '"current_allowed_artifacts":["nebula_colony/io/scenarios.py"],'
                    '"reason":"save_load owns loads_save"}'
                )
            }
        )

        self.assertIn("Agent reported a blocker", blocker)

    def test_integration_gate_fails_when_required_tests_all_skip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(tmpdir, "tests", "test_pkg.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import unittest\n\n"
                    "class SkippedTests(unittest.TestCase):\n"
                    "    @classmethod\n"
                    "    def setUpClass(cls):\n"
                    "        raise unittest.SkipTest('missing public api')\n\n"
                    "    def test_value(self):\n"
                    "        self.assertEqual(1, 1)\n"
                )

            gate = FinalGateSpec(
                required_artifacts=["pkg/__init__.py", "tests/test_pkg.py"],
                python_artifacts=["pkg/__init__.py", "tests/test_pkg.py"],
                package_roots=["pkg"],
                requires_tests=True,
            )

            result = GateChecker(tmpdir).check_final_gate(gate)

            self.assertFalse(result.ok)
            self.assertTrue(any("no executable tests ran" in error for error in result.errors))

    def test_generated_tests_must_not_replace_package_root_in_sys_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(tmpdir, "tests", "test_pkg.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import sys\n"
                    "import types\n"
                    "import unittest\n\n"
                    "sys.modules['pkg'] = types.ModuleType('pkg')\n\n"
                    "class PackageTests(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    "        self.assertTrue(True)\n"
                )

            item = WorkItem(
                id="coding:tests/test_pkg.py",
                kind="coding",
                scope_id="pkg",
                target_artifacts=["tests/test_pkg.py"],
            )
            result = InvariantChecker(tmpdir).check_self_check(item, {"changed_files": ["tests/test_pkg.py"]})

            self.assertFalse(result.ok)
            self.assertTrue(any("invalid_tests" in error for error in result.errors))

    def test_project_repair_artifact_filter_ignores_stdlib_traceback_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            steward = RunEngine(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))).auto_steward.recovery

            self.assertFalse(steward._looks_like_project_repair_artifact("rsions/3.11/lib/python3.11/importlib/__init__.py"))
            self.assertTrue(steward._looks_like_project_repair_artifact("nebula_colony/domain/buildings.py"))

    def test_scope_gate_runs_scope_local_tests_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(tmpdir, "tests", "test_pkg.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import unittest\n"
                    "import pkg\n\n"
                    "class PackageTests(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    "        self.assertEqual(pkg.VALUE, 1)\n"
                )

            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="pkg", type="code_module")],
                work_items=[
                    WorkItem(
                        id="coding:pkg/__init__.py",
                        kind="coding",
                        title="Package",
                        owner_profile="Backend_Engineer",
                        scope_id="pkg",
                        target_artifacts=["pkg/__init__.py"],
                    )
                ],
                team_gates=[
                    TeamGateSpec(
                        scope_id="pkg",
                        test_artifacts=["tests/test_pkg.py"],
                        deterministic_checks=[
                            "artifact_coverage",
                            "syntax_import",
                            "scope_tests",
                            "placeholder_scan",
                        ],
                    )
                ],
                final_gate=None,
            )
            scope = contract.scope_by_id()["pkg"]
            gate = contract.team_gates[0]

            result = GateChecker(tmpdir).check_team_gate(
                contract=contract,
                scope=scope,
                gate=gate,
                scope_items=contract.work_items,
            )

            self.assertTrue(result.ok)
            self.assertTrue(any("Unit test validation passed" in entry for entry in result.evidence))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding", "scope_reports", "pkg.json")))

    def test_smoke_scope_gate_defers_scope_tests_to_final(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="pkg", type="code_module")],
                work_items=[
                    WorkItem(
                        id="coding:pkg/__init__.py",
                        kind="coding",
                        title="Package",
                        owner_profile="Backend_Engineer",
                        scope_id="pkg",
                        target_artifacts=["pkg/__init__.py"],
                    )
                ],
                team_gates=[
                    TeamGateSpec(
                        scope_id="pkg",
                        test_artifacts=["tests/test_pkg.py"],
                        deterministic_checks=[
                            "artifact_coverage",
                            "syntax_import",
                            "functional_smoke",
                            "placeholder_scan",
                        ],
                    )
                ],
                final_gate=None,
            )
            scope = contract.scope_by_id()["pkg"]
            gate = contract.team_gates[0]

            result = GateChecker(tmpdir).check_team_gate(
                contract=contract,
                scope=scope,
                gate=gate,
                scope_items=contract.work_items,
            )

            self.assertTrue(result.ok)
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "tests", "test_pkg.py")))
            self.assertTrue(any("deferred to hardening/final" in entry for entry in result.evidence))

    def test_functional_smoke_catches_cross_scope_constructor_signature_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "domain"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "pkg", "interface"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "domain", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "interface", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "domain", "events.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "from dataclasses import dataclass\n\n"
                    "@dataclass\n"
                    "class DomainEvent:\n"
                    "    event_id: str\n"
                    "    event_type: str\n"
                )
            with open(os.path.join(tmpdir, "pkg", "interface", "cli.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "from pkg.domain.events import DomainEvent\n\n"
                    "def persist_simulation():\n"
                    "    return DomainEvent(event_type='simulation.persisted')\n"
                )
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="domain", type="code_module", artifacts=["pkg/domain/events.py"]),
                    WorkScope(id="interface", type="code_module", artifacts=["pkg/interface/cli.py"]),
                ],
                work_items=[
                    WorkItem(id="implement:interface", kind="coding", scope_id="interface", target_artifacts=["pkg/interface/cli.py"]),
                ],
                team_gates=[
                    TeamGateSpec(
                        scope_id="interface",
                        deterministic_checks=[
                            "artifact_coverage",
                            "syntax_import",
                            "functional_smoke",
                            "placeholder_scan",
                        ],
                    )
                ],
                final_gate=None,
            )

            result = GateChecker(tmpdir).check_team_gate(
                contract=contract,
                scope=contract.scope_by_id()["interface"],
                gate=contract.team_gates[0],
                scope_items=contract.work_items,
            )

            self.assertFalse(result.ok)
            self.assertTrue(any("Consumer contract smoke failed" in error for error in result.errors))
            self.assertTrue(any("event_id" in error for error in result.errors))

    def test_smoke_team_gate_does_not_generate_missing_scope_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="pkg", type="code_module", artifacts=["pkg/__init__.py"])],
                work_items=[
                    WorkItem(
                        id="implement:pkg",
                        kind="coding",
                        scope_id="pkg",
                        status="VERIFIED",
                        target_artifacts=["pkg/__init__.py"],
                    )
                ],
                team_gates=[
                    TeamGateSpec(
                        scope_id="pkg",
                        test_artifacts=["tests/test_pkg.py"],
                        deterministic_checks=[
                            "artifact_coverage",
                            "syntax_import",
                            "functional_smoke",
                            "placeholder_scan",
                        ],
                    )
                ],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            calls = []

            def forbidden_executor(*args):
                calls.append(args)
                return {"output": "unexpected"}

            runner = GateRunner(
                config=config,
                store=store,
                team_runtime=TeamRuntime(config, store),
                step_executor=forbidden_executor,
            )

            results = runner.run_ready_team_gates(run, contract)

            self.assertEqual(calls, [])
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok)
            self.assertEqual(store.get_gate(run_id, "team:pkg").status, "PASSED")

    def test_gate_only_scope_with_test_artifacts_can_run_team_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".contractcoding", "interfaces"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, ".contractcoding", "scaffolds"), exist_ok=True)
            with open(os.path.join(tmpdir, ".contractcoding", "interfaces", "planning.json"), "w", encoding="utf-8") as handle:
                handle.write('{"scope_id":"planning"}\n')
            with open(os.path.join(tmpdir, ".contractcoding", "scaffolds", "planning.json"), "w", encoding="utf-8") as handle:
                handle.write('{"scope_id":"planning"}\n')
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="planning", type="tests", artifacts=["tests/test_planner.py"])],
                work_items=[
                    WorkItem(
                        id="interface:planning",
                        kind="coding",
                        scope_id="planning",
                        status="VERIFIED",
                        target_artifacts=[".contractcoding/interfaces/planning.json"],
                    ),
                    WorkItem(
                        id="scaffold:planning",
                        kind="coding",
                        scope_id="planning",
                        status="VERIFIED",
                        target_artifacts=[".contractcoding/scaffolds/planning.json"],
                    ),
                ],
                team_gates=[
                    TeamGateSpec(
                        scope_id="planning",
                        test_artifacts=["tests/test_planner.py"],
                    )
                ],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            runner = GateRunner(
                config=config,
                store=store,
                team_runtime=TeamRuntime(config, store),
            )

            results = runner.run_ready_team_gates(run, contract)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok)
            self.assertEqual(store.get_gate(run_id, "team:planning").status, "PASSED")

    def test_phase_gate_waits_for_declared_team_gates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="core", type="code_module"),
                    WorkScope(id="interface", type="code_module"),
                ],
                work_items=[
                    WorkItem(id="core:item", kind="coding", scope_id="core", status="VERIFIED"),
                    WorkItem(id="interface:item", kind="coding", scope_id="interface", status="VERIFIED"),
                ],
                team_gates=[
                    TeamGateSpec(scope_id="core"),
                    TeamGateSpec(scope_id="interface"),
                ],
                phase_plan=[
                    PhaseContract(
                        phase_id="hardening",
                        teams_in_scope=["core", "interface"],
                        phase_gate={"checks": ["team_gates", "promotion_readiness"]},
                    )
                ],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_gate_status(run_id, "team:core", "PASSED")
            run = store.get_run(run_id)
            runner = GateRunner(
                config=config,
                store=store,
                team_runtime=TeamRuntime(config, store),
            )

            results = runner.run_ready_phase_gates(run, contract)

            self.assertEqual(results, [])
            self.assertEqual(store.get_gate(run_id, "phase:hardening").status, "PENDING")
            self.assertTrue(
                any(event.event_type == "phase_gate_waiting" for event in store.list_events(run_id, limit=10))
            )

    def test_final_gate_ignores_runtime_final_repair_team_gate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[
                    WorkScope(id="pkg", type="code_module"),
                    WorkScope(id="final_repair", type="code_module"),
                ],
                work_items=[
                    WorkItem(id="implement:pkg", kind="coding", scope_id="pkg", status="VERIFIED"),
                    WorkItem(id="final_repair:convergence", kind="coding", scope_id="final_repair", status="VERIFIED"),
                ],
                team_gates=[
                    TeamGateSpec(scope_id="pkg"),
                    TeamGateSpec(scope_id="final_repair"),
                ],
                final_gate=FinalGateSpec(required_artifacts=[], python_artifacts=[], package_roots=[], requires_tests=False),
                metadata={
                    "final_repair_mode": "centralized_convergence",
                    "runtime_final_repair_scopes": ["final_repair"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            team_runtime = TeamRuntime(config, store)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            team_runtime.ensure_teams(run_id, contract)
            for team in store.list_scope_team_runs(run_id, limit=20):
                store.update_team_run_status(team.id, "CLOSED")
            store.update_gate_status(run_id, "team:pkg", "PASSED")
            run = store.get_run(run_id)
            runner = GateRunner(config=config, store=store, team_runtime=team_runtime)

            result = runner.run_final_gate_if_ready(run, contract)

            self.assertTrue(result.ran)
            self.assertTrue(result.ok)
            self.assertEqual(store.get_gate(run_id, "final").status, "PASSED")

    def test_interface_conformance_respects_symbol_level_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "planner.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "class ColonyPlanner:\n"
                    "    def recommend(self, state):\n"
                    "        return []\n"
                )
            with open(os.path.join(tmpdir, "pkg", "policies.py"), "w", encoding="utf-8") as handle:
                handle.write("class PlanningPolicy:\n    pass\n")

            errors, evidence = InvariantChecker(tmpdir)._check_interface_conformance(
                [
                    {
                        "id": "ai.team",
                        "artifact": "pkg/planner.py",
                        "symbols": [
                            {"kind": "class", "name": "ColonyPlanner", "methods": ["recommend(state) -> list"]},
                            {"kind": "class", "name": "PlanningPolicy", "artifact": "pkg/policies.py"},
                        ],
                    }
                ]
            )

            self.assertEqual(errors, [])
            self.assertTrue(any("pkg/policies.py" in entry for entry in evidence))

    def test_final_diagnostic_prefers_traceback_owner_over_entrypoint_words(self):
        from ContractCoding.quality.diagnostics import FinalDiagnosticResolver

        diagnostics = FinalDiagnosticResolver.resolve(
            errors=[
                "Unit test validation failed for tests/test_integration.py:\n"
                "test_scenario_roundtrip_resume_ai_and_cli_smoke\n"
                "    recommendations_before = planner.recommend(scenario)\n"
                "  File \"/tmp/demo/aurora_forge/ai/planner.py\", line 58, in recommend\n"
                "    snapshot = _StateSnapshot.from_state(state)\n"
                "  File \"/tmp/demo/aurora_forge/ai/planner.py\", line 262, in _extract_int\n"
                "    return int(mapping[name])\n"
                "TypeError: int() argument must be a string, a bytes-like object or a real number, not 'dict'\n"
            ],
            required_artifacts=[
                "tests/test_integration.py",
                "aurora_forge/ai/planner.py",
                "aurora_forge/interface/cli.py",
            ],
            artifact_scope_map={
                "aurora_forge/ai/planner.py": "ai",
                "aurora_forge/interface/cli.py": "interface",
            },
        )

        self.assertEqual(diagnostics[0].primary_scope, "ai")
        self.assertEqual(diagnostics[0].suspected_scopes[0], "ai")

    def test_final_diagnostic_does_not_broaden_assertions_to_entire_scopes(self):
        from ContractCoding.quality.diagnostics import FinalDiagnosticResolver

        required = [
            "atlas_ops/domain/assets.py",
            "atlas_ops/domain/orders.py",
            "atlas_ops/domain/invariants.py",
            "atlas_ops/core/dispatch.py",
            "atlas_ops/core/routing.py",
            "atlas_ops/core/constraints.py",
            "atlas_ops/planning/planner.py",
            "atlas_ops/io/scenarios.py",
            "atlas_ops/interface/cli.py",
            "tests/test_integration.py",
        ]
        diagnostics = FinalDiagnosticResolver.resolve(
            errors=[
                "Unittest discovery failed:\n"
                "FAIL: test_cli_plan_uses_requested_scenario\n"
                "AssertionError: 'baseline' != '/tmp/task/scenario.json'\n"
                "FAIL: test_domain_to_engine_state_preserves_serialized_domain_data\n"
                "AssertionError: 5 != 2\n"
                "scenario path, total_population, engine dispatch"
            ],
            required_artifacts=required,
            artifact_scope_map={
                "atlas_ops/domain/assets.py": "domain",
                "atlas_ops/domain/orders.py": "domain",
                "atlas_ops/domain/invariants.py": "domain",
                "atlas_ops/core/dispatch.py": "core",
                "atlas_ops/core/routing.py": "core",
                "atlas_ops/core/constraints.py": "core",
                "atlas_ops/planning/planner.py": "planning",
                "atlas_ops/io/scenarios.py": "io",
                "atlas_ops/interface/cli.py": "interface",
            },
        )

        suspects = set(diagnostics[0].suspected_implementation_artifacts)

        self.assertIn("atlas_ops/io/scenarios.py", suspects)
        self.assertIn("atlas_ops/interface/cli.py", suspects)
        self.assertLess(len(suspects), 6)
        self.assertNotIn("atlas_ops/domain/invariants.py", suspects)
        self.assertNotIn("atlas_ops/core/constraints.py", suspects)

    def test_patch_guard_prefixes_bare_test_module_targets_from_tracebacks(self):
        from ContractCoding.runtime.repair import PatchGuard

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "tests", "test_integration.py"), "w", encoding="utf-8") as handle:
                handle.write("import unittest\n")
            guard = PatchGuard(
                tmpdir,
                diagnostic_text=(
                    "Unit test validation failed for tests/test_integration.py:\n"
                    "ERROR: test_case (test_integration.IntegrationContractTests.test_case)\n"
                ),
            )

            self.assertEqual(
                guard._targeted_unittest(),
                "tests.test_integration.IntegrationContractTests.test_case",
            )

    def test_patch_guard_discovers_tests_for_final_repair_without_precise_target(self):
        from ContractCoding.runtime.repair import PatchGuard

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "tests", "test_integration.py"), "w", encoding="utf-8") as handle:
                handle.write("import unittest\n")
            guard = PatchGuard(
                tmpdir,
                diagnostic_text="Gate `final` requested implementation_repair; integration behavior failed.",
            )

            self.assertEqual(guard._targeted_unittest(), "__discover_tests__")

    def test_team_gate_does_not_regenerate_existing_valid_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(tmpdir, "tests", "test_pkg.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import unittest\n"
                    "import pkg\n\n"
                    "class PackageTests(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    "        self.assertEqual(pkg.VALUE, 1)\n"
                )
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="pkg", type="code_module", artifacts=["pkg/__init__.py"])],
                work_items=[
                    WorkItem(
                        id="implement:pkg",
                        kind="coding",
                        scope_id="pkg",
                        status="VERIFIED",
                        target_artifacts=["pkg/__init__.py"],
                    )
                ],
                team_gates=[TeamGateSpec(scope_id="pkg", test_artifacts=["tests/test_pkg.py"])],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            run = store.get_run(run_id)
            calls = []

            def forbidden_executor(*args):
                calls.append(args)
                return {"output": "unexpected"}

            runner = GateRunner(
                config=config,
                store=store,
                team_runtime=TeamRuntime(config, store),
                step_executor=forbidden_executor,
            )

            result = runner._generate_gate_tests(
                run,
                contract,
                contract.team_gates[0],
                contract.work_items,
                tmpdir,
            )

            self.assertFalse(result.ran)
            self.assertEqual(calls, [])

    def test_steward_routes_gate_unittest_failure_to_implementation_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {"id": "pkg", "type": "code_module", "artifacts": ["pkg/__init__.py"]},
                        {"id": "tests", "type": "tests", "artifacts": ["tests/test_pkg.py"]},
                    ],
                    "work_items": [
                        {
                            "id": "coding:pkg/__init__.py",
                            "kind": "coding",
                            "title": "Package",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/__init__.py"],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "coding:tests/test_pkg.py",
                            "kind": "coding",
                            "title": "Tests",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "tests",
                            "status": "VERIFIED",
                            "target_artifacts": ["tests/test_pkg.py"],
                            "acceptance_criteria": ["tests exercise package"],
                        },
                    ],
                    "team_gates": [{"scope_id": "pkg", "test_artifacts": ["tests/test_pkg.py"]}],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            store.update_gate_status(
                run_id,
                "team:pkg",
                "FAILED",
                evidence=[
                    "Unit test validation failed for tests/test_pkg.py:\n"
                    "FAIL: test_value (tests.test_pkg.PackageTests.test_value)\n"
                    "AssertionError: 1 != 2"
                ],
            )
            step_id = store.create_step(run_id, "team:pkg", "System Verifier")
            store.finish_step(
                step_id,
                "ERROR",
                error="Unit test validation failed for tests/test_pkg.py:\nFAIL: test_value\nAssertionError: 1 != 2",
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 2},
            )

            self.assertTrue(recovered)
            implementation = store.get_work_item(run_id, "coding:pkg/__init__.py")
            self.assertEqual(implementation.status, "READY")
            self.assertIn("latest_diagnostic", implementation.inputs)
            self.assertIsNone(store.get_work_item(run_id, "coding:tests/test_pkg.py"))
            self.assertEqual(store.get_gate(run_id, "team:pkg").status, "PENDING")
            event = next(event for event in store.list_events(run_id) if event.event_type == "gate_repair_requested")
            self.assertEqual(event.payload["route"]["action"], "implementation_repair")
            self.assertEqual(event.payload["repair_plan"]["source_id"], "team:pkg")
            self.assertEqual(event.payload["repair_plan"]["reopened_items"], ["coding:pkg/__init__.py"])
            self.assertIn("repair_packet", implementation.inputs)

    def test_agent_blocker_does_not_redirect_to_other_work_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "systems.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "pkg", "type": "code_module", "artifacts": ["pkg/systems.py", "pkg/engine.py"]}],
                    "work_items": [
                        {
                            "id": "implement:pkg:systems",
                            "kind": "coding",
                            "scope_id": "pkg",
                            "status": "BLOCKED",
                            "target_artifacts": ["pkg/systems.py"],
                            "evidence": ["Target artifact exists: pkg/systems.py."],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "implement:pkg:engine",
                            "kind": "coding",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/engine.py"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "team_gates": [{"scope_id": "pkg"}],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            step_id = store.create_step(run_id, "implement:pkg:systems", "Implementation Worker")
            store.finish_step(
                step_id,
                "ERROR",
                error=(
                    'Agent reported a blocker: {"blocker_type":"out_of_scope_repair",'
                    '"required_artifacts":["pkg/engine.py"],'
                    '"current_allowed_artifacts":["pkg/systems.py"],'
                    '"reason":"engine owns the failing behavior"}'
                ),
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 2, "contract_replan_limit": 1},
            )

            self.assertFalse(recovered)
            self.assertEqual(store.get_work_item(run_id, "implement:pkg:systems").status, "BLOCKED")
            self.assertEqual(store.get_work_item(run_id, "implement:pkg:engine").status, "VERIFIED")
            events = store.list_events(run_id)
            self.assertFalse(any(event.event_type == "agent_blocker_redirected" for event in events))
            self.assertTrue(any(event.event_type == "needs_human" for event in events))

    def test_agent_blocker_to_tests_does_not_enable_test_repair_from_item_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "io.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "io", "type": "code_module", "artifacts": ["pkg/io.py"]}],
                    "work_items": [
                        {
                            "id": "implement:io",
                            "kind": "coding",
                            "scope_id": "io",
                            "status": "BLOCKED",
                            "target_artifacts": ["pkg/io.py"],
                            "evidence": ["Target artifact exists: pkg/io.py."],
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "team_gates": [{"scope_id": "io", "test_artifacts": ["tests/test_io.py"]}],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            step_id = store.create_step(run_id, "implement:io", "Implementation Worker")
            store.finish_step(
                step_id,
                "ERROR",
                error=(
                    'Agent reported a blocker: {"blocker_type":"out_of_scope_repair",'
                    '"required_artifacts":["tests/test_io.py"],'
                    '"current_allowed_artifacts":["pkg/io.py"],'
                    '"reason":"the generated test asserts TemporaryDirectory paths after cleanup"}'
                ),
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 2, "contract_replan_limit": 1},
            )

            self.assertFalse(recovered)
            self.assertEqual(store.get_work_item(run_id, "implement:io").status, "BLOCKED")
            gate = store.get_gate(run_id, "team:io")
            self.assertEqual(gate.status, "PENDING")
            self.assertFalse(gate.metadata.get("allow_test_repair"))
            self.assertFalse(any(event.event_type == "agent_blocker_redirected" for event in store.list_events(run_id)))

    def test_invalid_agent_blocker_retries_same_work_item_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "core.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            with open(os.path.join(tmpdir, "pkg", "exports.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 2\n")
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "pkg", "type": "code_module", "artifacts": ["pkg/core.py", "pkg/exports.py"]}],
                    "work_items": [
                        {
                            "id": "implement:pkg:core",
                            "kind": "coding",
                            "scope_id": "pkg",
                            "status": "BLOCKED",
                            "target_artifacts": ["pkg/core.py", "pkg/exports.py"],
                            "evidence": ["Target artifact exists: pkg/core.py."],
                            "acceptance_criteria": ["done"],
                        },
                        {
                            "id": "implement:pkg:other",
                            "kind": "coding",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/other.py"],
                            "acceptance_criteria": ["done"],
                        },
                    ],
                    "team_gates": [{"scope_id": "pkg"}],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            step_id = store.create_step(run_id, "implement:pkg:core", "Implementation Worker")
            store.finish_step(
                step_id,
                "ERROR",
                error=(
                    "Invalid blocker: required artifacts are already allowed for this WorkItem. "
                    "Continue the repair inside the allowed target files."
                ),
            )
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 2, "contract_replan_limit": 1},
            )

            self.assertTrue(recovered)
            self.assertEqual(store.get_work_item(run_id, "implement:pkg:core").status, "READY")
            self.assertEqual(store.get_work_item(run_id, "implement:pkg:other").status, "VERIFIED")
            self.assertFalse(any(event.event_type == "agent_blocker_redirected" for event in store.list_events(run_id)))

    def test_system_artifact_repair_ignores_context_only_artifacts(self):
        from ContractCoding.quality.diagnostics import DiagnosticRecord

        with tempfile.TemporaryDirectory() as tmpdir:
            steward = RunEngine(Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))).auto_steward.recovery
            assertion = DiagnosticRecord(
                gate_id="team:io",
                scope_id="io",
                failure_kind="unittest_assertion",
                traceback_excerpt="AssertionError: False is not true",
                affected_artifacts=[".contractcoding/interfaces/io.json", "tests/test_io.py"],
            )
            missing = DiagnosticRecord(
                gate_id="team:io",
                scope_id="io",
                failure_kind="missing_artifact",
                traceback_excerpt="Required artifact missing: .contractcoding/interfaces/io.json",
                affected_artifacts=[".contractcoding/interfaces/io.json"],
            )

            self.assertFalse(steward._is_system_artifact_failure([assertion]))
            self.assertTrue(steward._is_system_artifact_failure([missing]))

    def test_steward_blocks_team_gate_repair_without_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [{"id": "pkg", "type": "code_module", "artifacts": ["pkg/__init__.py"]}],
                    "work_items": [
                        {
                            "id": "coding:pkg/__init__.py",
                            "kind": "coding",
                            "title": "Package",
                            "owner_profile": "Backend_Engineer",
                            "scope_id": "pkg",
                            "status": "VERIFIED",
                            "target_artifacts": ["pkg/__init__.py"],
                            "acceptance_criteria": ["done"],
                        }
                    ],
                    "team_gates": [{"scope_id": "pkg"}],
                    "acceptance_criteria": ["done"],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            store.update_run_status(run_id, "BLOCKED")
            store.update_gate_status(run_id, "team:pkg", "FAILED")
            engine = RunEngine(config=config, store=store, step_executor=lambda *_: {"output": "noop"})

            recovered = engine.auto_steward.recovery._recover_without_replan(
                run_id,
                engine.status(run_id),
                {"infra_retry_limit": 2, "item_repair_limit": 2, "test_repair_limit": 4, "contract_replan_limit": 2},
            )

            self.assertFalse(recovered)
            self.assertEqual(store.get_work_item(run_id, "coding:pkg/__init__.py").status, "VERIFIED")
            self.assertEqual(store.get_gate(run_id, "team:pkg").status, "BLOCKED")
            self.assertTrue(store.get_gate(run_id, "team:pkg").metadata.get("diagnostic_blocked"))

    def test_team_executor_runs_unittest_for_python_test_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            item = WorkItem(
                id="coding:test_demo.py",
                kind="coding",
                title="Test artifact",
                owner_profile="Backend_Engineer",
                scope_id="tests",
                status="READY",
                target_artifacts=["test_demo.py"],
                acceptance_criteria=["tests run"],
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, work_items=[item])
            run = store.get_run(run_id)
            wave = TeamWave(
                run_id=run_id,
                scope=WorkScope(id="tests", type="code_module"),
                items=[item],
                execution_plane="workspace",
                profiles=["Backend_Engineer"],
                parallel_slots=1,
            )

            def executor(item, agent_name, state):
                with open(os.path.join(tmpdir, "test_demo.py"), "w", encoding="utf-8") as handle:
                    handle.write(
                        "import unittest\n\n"
                        "class DemoTests(unittest.TestCase):\n"
                        "    def test_truth(self):\n"
                        "        self.assertTrue(True)\n"
                    )
                return {"output": "created test_demo.py"}

            result = TeamExecutor(config=config, store=store, step_executor=executor).execute(run, wave)

            self.assertTrue(result.ok)
            item = store.get_work_item(run_id, "coding:test_demo.py")
            self.assertEqual(item.status, "VERIFIED")
            self.assertTrue(any("Unit test validation passed" in entry for entry in item.evidence))

    def test_gate_review_parser_rejects_negative_gate_review_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            verdict = GateReviewParser().parse(
                {"output": '<gate_review>{"verdict":"blocked","block_reason":"missing_behavior"}</gate_review>'},
                {"review_layer": "team", "allowed_block_reasons": ["missing_behavior"]},
            )

            self.assertFalse(verdict.accepted)
            self.assertIn("missing_behavior", verdict.error)

    def test_tool_governor_blocks_writes_in_suggest_mode(self):
        governor = ToolGovernor(approval_mode="suggest")

        self.assertTrue(governor.decide("read_file", {"path": "app.py"}).allowed)
        write_decision = governor.decide("write_file", {"path": "app.py"})
        self.assertFalse(write_decision.allowed)
        self.assertTrue(write_decision.requires_approval)

    def test_tool_governor_blocks_writes_outside_artifact_scope(self):
        governor = ToolGovernor(
            approval_mode="auto-edit",
            allowed_artifacts=["src/app.py"],
            allowed_conflict_keys=["artifact:src/app.py"],
        )

        self.assertTrue(governor.decide("write_file", {"path": "src/app.py"}).allowed)
        decision = governor.decide("write_file", {"path": "src/other.py"})
        self.assertFalse(decision.allowed)
        self.assertIn("outside", decision.reason)

    def test_tool_governor_preserves_hidden_contractcoding_paths(self):
        governor = ToolGovernor(
            approval_mode="auto-edit",
            allowed_artifacts=[".contractcoding/output.md"],
            allowed_conflict_keys=["artifact:.contractcoding/output.md"],
        )

        self.assertTrue(governor.decide("write_file", {"path": ".contractcoding/output.md"}).allowed)
        self.assertTrue(governor.decide("write_file", {"path": "./.contractcoding/output.md"}).allowed)

    def test_tool_governor_allows_search_in_auto_edit_mode(self):
        governor = ToolGovernor(approval_mode="auto-edit")

        self.assertTrue(governor.decide("search_web", {"query": "agent reliability"}).allowed)

    def test_research_source_blocker_is_human_required_not_item_repair(self):
        self.assertEqual(
            HealthMonitor._classify_failure(
                "Target artifact missing. Research source access unavailable; requires approved source access "
                "or provided source material."
            ),
            FAILURE_HUMAN_REQUIRED,
        )

    def test_context_manager_filters_skills_by_work_kind_and_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                CONTEXT_SKILL_CHARS=80,
                ENABLE_BUILTIN_SKILLS=False,
            )
            manager = ContextManager(config, agents=["Backend_Engineer"])
            manager.register_skill(
                SkillSpec(
                    name="Python Coding",
                    description="Use project-native Python style.",
                    prompt="Prefer unittest and small pure functions.",
                    allowed_work_kinds=["coding"],
                )
            )
            manager.register_skill(
                SkillSpec(
                    name="Research Notes",
                    description="Use citations.",
                    prompt="Collect sources.",
                    allowed_work_kinds=["research"],
                )
            )

            rendered = manager.render_skill_context("coding")

            self.assertIn("Python Coding", rendered)
            self.assertNotIn("Research Notes", rendered)
            self.assertLessEqual(len(rendered), config.CONTEXT_SKILL_CHARS + len("\n\n[context truncated]\n"))

    def test_llm_agent_extracts_final_test_targets_and_enforces_test_engineer_completion(self):
        from ContractCoding.agents.agent import LLMAgent

        prompt = (
            "Final integration gate\n"
            "Wave allowed artifacts:\n"
            "- tests/test_domain.py\n"
            "- tests/test_engine.py\n\n"
            "Task: write missing final integration tests.\n"
            "Target files:\n"
            "- tests/test_domain.py\n"
            "- tests/test_engine.py\n\n"
            "- allowed_tools: create_file, replace_file\n"
            "- conflict_keys: artifact:tests/test_domain.py, artifact:tests/test_engine.py\n"
        )

        targets, conflict_keys, allowed_tools = LLMAgent._extract_artifact_policy(prompt)

        self.assertEqual(targets, ["tests/test_domain.py", "tests/test_engine.py"])
        self.assertEqual(conflict_keys, ["artifact:tests/test_domain.py", "artifact:tests/test_engine.py"])
        self.assertEqual(allowed_tools, ["create_file", "replace_file"])
        self.assertTrue(LLMAgent._agent_enforces_target_completion("Test_Engineer"))

    def test_skill_router_selects_test_generation_workflow_for_test_engineer_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = ContextManager(config, agents=["Test_Engineer"])
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="tests", type="tests", artifacts=["tests/test_core.py"])],
                work_items=[
                    WorkItem(
                        id="gate:final:tests",
                        kind="coding",
                        title="Generate final integration gate tests",
                        owner_profile="Test_Engineer",
                        scope_id="tests",
                        target_artifacts=["tests/test_core.py"],
                        acceptance_criteria=["test public behavior"],
                        team_role_hint="test_worker",
                    )
                ],
                final_gate=FinalGateSpec(required_artifacts=["tests/test_core.py"], requires_tests=True),
            )
            item = contract.work_items[0]

            packet = manager.build_agent_input_packet(
                task="demo",
                contract=contract,
                item=item,
                scope=contract.scope_by_id()[item.scope_id],
                wave_kind="implementation",
                runtime_items=contract.work_items,
            )

            self.assertIn("coding.test_generation_workflow", packet.selected_skills)
            self.assertIn("every target test file exists", packet.skill_context)
            self.assertNotIn("coding.code_generation_workflow", packet.selected_skills)

    def test_skill_router_selects_code_generation_workflow_for_implementation_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = ContextManager(config, agents=["Backend_Engineer"])
            contract = ContractSpec(
                goals=["demo"],
                work_scopes=[WorkScope(id="core", type="code_module", artifacts=["pkg/core.py"])],
                work_items=[
                    WorkItem(
                        id="implement:core",
                        kind="coding",
                        title="Implement core",
                        owner_profile="Backend_Engineer",
                        scope_id="core",
                        target_artifacts=["pkg/core.py"],
                        acceptance_criteria=["core imports cleanly"],
                    )
                ],
            )
            item = contract.work_items[0]

            packet = manager.build_agent_input_packet(
                task="demo",
                contract=contract,
                item=item,
                scope=contract.scope_by_id()[item.scope_id],
                wave_kind="implementation",
                runtime_items=contract.work_items,
            )

            self.assertIn("coding.code_generation_workflow", packet.selected_skills)
            self.assertIn("symbol-closure pass", packet.skill_context)

    def test_context_manager_loads_skill_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = os.path.join(tmpdir, "SKILL.md")
            with open(skill_path, "w", encoding="utf-8") as handle:
                handle.write("# Docs Skill\nallowed_work_kinds: doc\nWrite concise docs.\n")

            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                SKILL_PATHS=skill_path,
                ENABLE_BUILTIN_SKILLS=False,
            )
            manager = ContextManager(config, agents=["Technical_Writer"])

            self.assertEqual([skill.name for skill in manager.skills_for("doc")], ["Docs Skill"])
            self.assertEqual(manager.skills_for("coding"), [])

    def test_context_manager_loads_frontmatter_skill_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = os.path.join(tmpdir, "SKILL.md")
            with open(skill_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "---\n"
                    "name: code-review\n"
                    "description: Reviews code and gate evidence. Use for reviewer and final gate tasks.\n"
                    "allowed_work_kinds: [eval, coding]\n"
                    "trigger_keywords:\n"
                    "  - review\n"
                    "  - gate\n"
                    "evidence_requirements: deterministic evidence, blocking finding\n"
                    "tool_hints: read_file, run_code\n"
                    "risk_policy: Never pass deterministic failures.\n"
                    "priority: 7\n"
                    "---\n"
                    "# Code Review\n"
                    "Use deterministic evidence before narrative judgment.\n"
                )

            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                SKILL_PATHS=skill_path,
                ENABLE_BUILTIN_SKILLS=False,
            )
            manager = ContextManager(config, agents=["Reviewer"])
            skill = manager.skills_for("eval")[0]

            self.assertEqual(skill.name, "code-review")
            self.assertEqual(skill.priority, 7)
            self.assertEqual(skill.trigger_keywords, ["review", "gate"])
            self.assertIn("run_code", skill.tool_hints)
            self.assertIn("deterministic evidence", skill.render())

    def test_context_manager_loads_builtin_mvp_skills_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = ContextManager(config, agents=["Backend_Engineer"])

            coding_names = {skill.name for skill in manager.skills_for("coding")}
            research_names = {skill.name for skill in manager.skills_for("research")}
            doc_names = {skill.name for skill in manager.skills_for("doc")}

            self.assertIn("general.delivery", coding_names)
            self.assertIn("coding.implementation", coding_names)
            self.assertIn("coding.public_entrypoint_acceptance", coding_names)
            self.assertIn("coding.integration_boundary", coding_names)
            self.assertIn("coding.large_project_generation", coding_names)
            self.assertIn("coding.code_generation_workflow", coding_names)
            self.assertIn("coding.test_generation_workflow", coding_names)
            self.assertIn("coding.public_entrypoint_acceptance", {skill.name for skill in manager.skills_for("eval")})
            self.assertIn("coding.review_gate_workflow", {skill.name for skill in manager.skills_for("eval")})
            self.assertIn("research.synthesis", research_names)
            self.assertIn("paper.writing", doc_names)
            self.assertIn("math.reasoning", doc_names)

    def test_public_entrypoint_skill_captures_large_project_lessons(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = ContextManager(config, agents=["Backend_Engineer"])

            rendered = manager.render_skill_context("coding", max_chars=20000)

            self.assertIn("coding.public_entrypoint_acceptance", rendered)
            self.assertIn("cli run <scenario>", rendered)
            self.assertIn("scenario description -> runtime state -> engine mutation", rendered)
            self.assertIn("no padding, dead branches, fake tests", rendered)
            self.assertIn("exact producer signature", rendered)
            self.assertIn("CLI/API input -> domain event/dataclass constructor", rendered)

    def test_prompt_builder_adds_phase_overlays(self):
        builder = AgentPromptBuilder(
            agent_name="Backend_Engineer",
            agent_prompt="role",
            system_prompt=CORE_SYSTEM_PROMPT,
        )
        implementation_messages = builder.build(
            task_description="demo",
            current_task="Module team: core\nImplement/Fix app.py",
            next_available_agents=[],
        )

        verifier = AgentPromptBuilder(
            agent_name="Critic",
            agent_prompt="role",
            system_prompt=CORE_SYSTEM_PROMPT,
        )
        verification_messages = verifier.build(
            task_description="demo",
            current_task="Verify completed work item: a\nKind: coding",
            next_available_agents=[],
        )

        self.assertIn("Runtime Phase: Team Execution", implementation_messages[0]["content"])
        self.assertIn("Runtime Phase: Verification", verification_messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
