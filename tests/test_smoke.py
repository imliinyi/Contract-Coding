"""End-to-end smoke test for the registry-based runtime.

Exercises:
  - onboarding + plan freeze
  - team activation (working_paper + task_ledger seeded)
  - one orchestration tick with NullLLMPort (offline)
  - status / events / escalations queries

Uses a temporary workspace so it never touches the real registry.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ContractCoding.app import ContractCodingService
from ContractCoding.agents.coordinator import make_team_tools
from ContractCoding.agents.reviewer import make_pass as make_reviewer_pass
from ContractCoding.config import Config
from ContractCoding.contract.capsule import (
    CapsuleArtifacts,
    CapsuleInterface,
    CapsuleTag,
    ExecutableExample,
    InterfaceCapsuleV2,
)
from ContractCoding.contract.project import BoundedContext, Invariant
from ContractCoding.memory.ledgers import TaskItem, TaskStatus
from ContractCoding.worker import NullLLMPort, PipelineConfig, WorkerPipeline


class SmokeE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="cc-smoke-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config = Config(WORKSPACE_DIR=self.tmp, OFFLINE_LLM=True)
        self.service = ContractCodingService(self.config)

    def _onboard(self) -> None:
        self.service.onboard(
            goal="ship a tiny calculator service",
            bounded_contexts=[
                BoundedContext(
                    team_id="domain",
                    purpose_one_liner="own arithmetic core",
                    capability_names=["add", "mul"],
                    allowed_consumers=["api"],
                    workspace_path="/workspace/domain/",
                ),
                BoundedContext(
                    team_id="api",
                    purpose_one_liner="expose HTTP endpoints",
                    capability_names=["calc_endpoint"],
                    allowed_consumers=["*"],
                    workspace_path="/workspace/api/",
                ),
            ],
            invariants=[
                Invariant(
                    id="inv1",
                    description="all responses include a request_id",
                    scope="global",
                ),
            ],
            acceptance_signals=["unit tests pass"],
        )

    def test_onboard_activate_tick(self) -> None:
        self._onboard()
        status = self.service.status()
        self.assertIsNotNone(status["plan"])
        self.assertTrue(status["plan"]["frozen"])
        self.assertEqual(len(status["teams"]), 2)

        self.service.activate_team(
            "domain",
            initial_tasks=[
                TaskItem(
                    task_id="t1",
                    title="implement add",
                    goal="implement and test add(a, b)",
                    output_format="python",
                ),
            ],
        )
        self.service.activate_team(
            "api",
            initial_tasks=[
                TaskItem(
                    task_id="t2",
                    title="wire endpoint",
                    goal="wire calc_endpoint to domain.add",
                    output_format="python",
                    capsule_dependencies=["domain:add"],
                ),
            ],
        )

        report = self.service.tick(offline=True)
        self.assertGreaterEqual(report.ran_tasks, 1)

        domain_progress = self.service.coordinator.tool.read_progress("domain")
        role_by_kind = {entry.kind: entry.margin.author_role.value for entry in domain_progress}
        self.assertEqual(role_by_kind.get("inspector"), "inspector")
        self.assertEqual(role_by_kind.get("planner"), "planner")
        self.assertEqual(role_by_kind.get("implementer"), "implementer")
        self.assertEqual(role_by_kind.get("reviewer"), "reviewer")
        self.assertEqual(role_by_kind.get("judge"), "judge")

        api_ledger = self.service.coordinator.tool.get_task_ledger("api")
        self.assertIsNotNone(api_ledger)
        assert api_ledger is not None
        self.assertEqual(api_ledger.by_id("t2").status, TaskStatus.PENDING)
        api_progress_kinds = {
            entry.kind for entry in self.service.coordinator.tool.read_progress("api")
        }
        self.assertNotIn("inspector", api_progress_kinds)
        self.assertNotIn("judge", api_progress_kinds)
        self.assertNotIn("planner", api_progress_kinds)
        self.assertNotIn("implementer", api_progress_kinds)
        obligations = self.service.coordinator.tool.read_obligations()
        self.assertTrue(
            any(o.kind.value == "missing_capsule" and o.team_id == "api" for o in obligations)
        )

        events = self.service.events(limit=20)
        self.assertTrue(any(e.get("kind") == "team_activated" for e in events))
        kinds = {e.get("kind") for e in events}
        self.assertIn("slice_started", kinds)

        escalations = self.service.list_escalations()
        self.assertIsInstance(escalations, list)

    def test_orchestrate_terminates(self) -> None:
        self._onboard()
        self.service.activate_team("domain")
        self.service.activate_team("api")
        reports = self.service.orchestrate(offline=True, max_ticks=3)
        self.assertGreaterEqual(len(reports), 1)

    def test_capsule_publish_unblocks_dependent_work_next_tick(self) -> None:
        self._onboard()
        self.service.activate_team("domain")
        self.service.activate_team(
            "api",
            initial_tasks=[
                TaskItem(
                    task_id="t2",
                    title="wire endpoint",
                    goal="wire calc_endpoint to domain.add",
                    output_format="python",
                    capsule_dependencies=["domain:add"],
                ),
            ],
        )

        first = self.service.tick(offline=True)
        self.assertEqual(first.ran_tasks, 0)
        self.assertEqual(first.blocked, 1)

        capsule = InterfaceCapsuleV2(
            capsule_id="cap:domain:add:test",
            team_id="domain",
            capability="add",
            tag=CapsuleTag(
                name="add",
                one_line_purpose="add two numbers",
                key_capabilities=["add"],
            ),
            interface=CapsuleInterface(
                name="add",
                interface_def={"function": "add", "signature": "add(a: int, b: int) -> int"},
                examples=[
                    ExecutableExample(name="positive", invocation="add(1, 2)", expected="3"),
                    ExecutableExample(name="zero", invocation="add(0, 0)", expected="0"),
                ],
            ),
            artifacts=CapsuleArtifacts(
                stub_package="stubs/domain/add",
                smoke_tests="smoke/domain/test_add.py",
                manifest="contracts/domain/add/MANIFEST.md",
            ),
        )
        self.service.coordinator.tool.publish_capsule(capsule)

        second = self.service.tick(offline=True)
        self.assertEqual(second.ran_tasks, 1)
        api_progress_kinds = {
            entry.kind for entry in self.service.coordinator.tool.read_progress("api")
        }
        self.assertIn("inspector", api_progress_kinds)
        updated = self.service.coordinator.tool.get_capsule("domain", "add")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIn("api", updated.consumers)

    def test_artifacts_without_validation_are_blocked(self) -> None:
        self._onboard()
        self.service.activate_team(
            "domain",
            initial_tasks=[
                TaskItem(
                    task_id="t1",
                    title="write artifact",
                    goal="write a small artifact",
                    output_format="python",
                ),
            ],
        )
        plan = self.service.coordinator.tool.get_plan()
        self.assertIsNotNone(plan)
        assert plan is not None
        ctx = plan.context_of("domain")
        ledger = self.service.coordinator.tool.get_task_ledger("domain")
        self.assertIsNotNone(ledger)
        assert ledger is not None
        task = ledger.by_id("t1")
        self.assertIsNotNone(task)
        assert task is not None

        tools = make_team_tools(self.service.backend, self.service.acl, "domain")
        implementer_llm = NullLLMPort(
            fixed_text=(
                '{"artifacts": [{"path": "src/example.py", '
                '"content": "VALUE = 1\\n", "intent": "example", '
                '"is_test": false}], "decisions": [], "uncertainty": 0.1}'
            )
        )
        reviewer = make_reviewer_pass(
            tools.reviewer,
            NullLLMPort(fixed_text='{"concerns": [], "anti_patterns": [], "closed": []}'),
            prompts=self.service.prompts,
        )
        pipeline = WorkerPipeline(
            tool=tools.implementer,
            llm=implementer_llm,
            reviewer=reviewer,
            control_tool=tools.planner,
            planner_tool=tools.planner,
            inspector_tool=tools.inspector,
            implementer_tool=tools.implementer,
            judge_tool=tools.judge,
            config=PipelineConfig(require_validation=True),
            prompts=self.service.prompts,
            skills=self.service.skills,
        )

        packet = self.service.coordinator._build_packet(plan, ctx, "domain", task)
        self.assertIsNotNone(packet)
        assert packet is not None
        result = pipeline.run(packet)

        self.assertFalse(result.verdict.approved)
        self.assertIn("validation evidence missing", result.verdict.blockers)
        updated = self.service.coordinator.tool.get_task_ledger("domain").by_id("t1")
        self.assertEqual(updated.status, TaskStatus.BLOCKED)

    def test_no_artifact_failure_gets_one_repair_retry(self) -> None:
        self._onboard()
        self.service.coordinator.max_item_repair_attempts = 1
        self.service.activate_team(
            "domain",
            initial_tasks=[
                TaskItem(
                    task_id="t1",
                    title="write artifact",
                    goal="write a small artifact",
                    output_format="python",
                ),
            ],
        )

        first = self.service.tick(offline=True)

        self.assertEqual(first.rejected, 1)
        contract = self.service.coordinator.tool.get_team_contract("domain")
        self.assertIsNotNone(contract)
        assert contract is not None
        item = contract.by_task_id("t1")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.status.value, "pending")
        self.assertEqual(item.phase.value, "repair")
        ledger_item = self.service.coordinator.tool.get_task_ledger("domain").by_id("t1")
        self.assertEqual(ledger_item.status, TaskStatus.PENDING)

        second = self.service.tick(offline=True)

        self.assertEqual(second.rejected, 1)
        contract = self.service.coordinator.tool.get_team_contract("domain")
        assert contract is not None
        item = contract.by_task_id("t1")
        assert item is not None
        self.assertEqual(item.status.value, "blocked")


if __name__ == "__main__":
    unittest.main()
