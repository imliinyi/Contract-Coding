from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ContractCoding.agents.auditor import ContractAuditor
from ContractCoding.agents.reducer import ContractReducer
from ContractCoding.agents.scheduler import SchedulerConfig, TeamScheduler
from ContractCoding.core.margin import AgentRole
from ContractCoding.contract.evidence import ValidationEvidence
from ContractCoding.contract.kernel import ContractKernel, ProjectContract, TeamContract
from ContractCoding.contract.operation import (
    ContractObligation,
    ContractOperation,
    ObligationKind,
    OperationKind,
    OperationStatus,
)
from ContractCoding.contract.project import BoundedContext, IntentLedger
from ContractCoding.contract.work import ConflictKey, TeamWorkItem
from ContractCoding.contract.team import TeamSubContract
from ContractCoding.memory.ledgers import TaskItem
from ContractCoding.registry import Actor, RegistryACL, RegistryBackend, RegistryTool
from ContractCoding.registry.backend import RegistryPath
from ContractCoding.worker.packet import ContextPacket, SliceArtifact
from ContractCoding.worker.passes import ImplementerPass, JudgePass
from ContractCoding.worker.protocol import NullLLMPort


class ContractRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="cc-contract-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.backend = RegistryBackend(self.tmp)
        self.acl = RegistryACL()
        self.tool = RegistryTool(self.backend, self.acl, Actor.coordinator())
        self.auditor = ContractAuditor(self.tool)
        self.reducer = ContractReducer(self.tool, self.auditor)

    def _project(self) -> ProjectContract:
        return ProjectContract(
            intent=IntentLedger(goal="ship"),
            bounded_contexts=[
                BoundedContext("domain", "domain", ["add"], ["api"], "/workspace/domain/"),
                BoundedContext("api", "api", ["endpoint"], ["*"], "/workspace/api/"),
            ],
            frozen=True,
        )

    def test_reducer_accepts_valid_declare_api_and_updates_team_contract(self) -> None:
        self.tool.write_team_contract(TeamContract.empty("domain"))
        self.backend.write_text(RegistryPath("/workspace/domain/api.py"), "def add(a, b):\n    return a + b\n")
        op = ContractOperation.new(
            kind=OperationKind.DECLARE_API,
            from_team="domain",
            target_ref="domain/add",
            payload={"capability": "add", "symbols": ["add"], "files": ["api.py"]},
            evidence_refs=["api.py"],
        )

        result = self.reducer.process(op)

        self.assertTrue(result.accepted)
        contract = self.tool.get_team_contract("domain")
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertIn("add", contract.public_apis)

    def test_submit_evidence_without_evidence_is_rejected_without_polluting_contract(self) -> None:
        item = TeamWorkItem(
            work_id="domain:t1",
            team_id="domain",
            source_task_id="t1",
            title="write",
            goal="write code",
        )
        self.tool.write_team_contract(TeamContract(team_id="domain", work_items=[item]))
        op = ContractOperation.new(
            kind=OperationKind.SUBMIT_EVIDENCE,
            from_team="domain",
            target_ref="work:domain:t1",
            related_task_ids=["t1"],
        )

        result = self.reducer.process(op)

        self.assertFalse(result.accepted)
        contract = self.tool.get_team_contract("domain")
        assert contract is not None
        self.assertEqual(contract.by_task_id("t1").status.value, "pending")

    def test_non_owner_declare_api_is_rejected(self) -> None:
        self.tool.write_team_contract(TeamContract.empty("domain"))
        self.backend.write_text(RegistryPath("/workspace/api/api.py"), "def add(a, b):\n    return a + b\n")
        op = ContractOperation.new(
            kind=OperationKind.DECLARE_API,
            from_team="api",
            target_team="domain",
            target_ref="domain/add",
            payload={"capability": "add", "symbols": ["add"], "files": ["api.py"]},
            evidence_refs=["api.py"],
        )

        result = self.reducer.process(op)

        self.assertFalse(result.accepted)
        contract = self.tool.get_team_contract("domain")
        assert contract is not None
        self.assertNotIn("add", contract.public_apis)

    def test_auditor_derives_and_resolves_validation_obligation(self) -> None:
        item = TeamWorkItem(
            work_id="domain:t1",
            team_id="domain",
            source_task_id="t1",
            title="write",
            goal="write code",
            writes=["src/example.py"],
        )
        kernel = ContractKernel(
            project=self._project(),
            teams={"domain": TeamContract(team_id="domain", work_items=[item])},
        )

        obligations = self.auditor.derive_obligations(kernel)
        self.assertTrue(any(o.kind == ObligationKind.VALIDATION_MISSING for o in obligations))

        op = ContractOperation.new(
            kind=OperationKind.SUBMIT_EVIDENCE,
            from_team="domain",
            target_ref="work:domain:t1",
            related_task_ids=["t1"],
            evidence_refs=["validation:pytest"],
        )
        op.accept()
        resolved = self.auditor.resolve_obligations(obligations, [op])
        self.assertTrue(all(o.status.value == "resolved" for o in resolved))

    def test_scheduler_packs_disjoint_items_and_splits_conflicts(self) -> None:
        scheduler = TeamScheduler(SchedulerConfig(max_parallel_teams=4))
        left = TeamWorkItem(
            work_id="domain:t1",
            team_id="domain",
            source_task_id="t1",
            title="domain",
            goal="domain",
            writes=["domain/a.py"],
        )
        right = TeamWorkItem(
            work_id="api:t2",
            team_id="api",
            source_task_id="t2",
            title="api",
            goal="api",
            writes=["api/b.py"],
        )
        kernel = ContractKernel(
            project=self._project(),
            teams={
                "domain": TeamContract(team_id="domain", work_items=[left]),
                "api": TeamContract(team_id="api", work_items=[right]),
            },
        )

        report = scheduler.schedule(kernel)
        self.assertEqual(len(report.waves), 1)
        self.assertEqual(len(report.waves[0].items), 2)

        right.writes = ["domain/a.py"]
        report = scheduler.schedule(kernel)
        self.assertEqual(len(report.waves), 2)

    def test_scheduler_blocks_obligations_and_serializes_uncertain_items(self) -> None:
        scheduler = TeamScheduler(SchedulerConfig(max_parallel_teams=4))
        blocked = TeamWorkItem(
            work_id="api:t2",
            team_id="api",
            source_task_id="t2",
            title="api",
            goal="api",
            capsule_dependencies=["domain:add"],
        )
        uncertain = TeamWorkItem(
            work_id="domain:t1",
            team_id="domain",
            source_task_id="t1",
            title="domain",
            goal="domain",
            writes=["domain/a.py"],
            uncertainty=0.9,
            conflict_keys=[ConflictKey("module", "domain")],
        )
        kernel = ContractKernel(
            project=self._project(),
            teams={
                "api": TeamContract(team_id="api", work_items=[blocked]),
                "domain": TeamContract(team_id="domain", work_items=[uncertain]),
            },
            obligations=[
                ContractObligation.new(
                    kind=ObligationKind.MISSING_CAPSULE,
                    team_id="api",
                    target_ref="domain:add",
                    task_ids=["t2"],
                    reason="missing",
                )
            ],
        )

        report = scheduler.schedule(kernel)
        self.assertEqual(len(report.blocked), 1)
        self.assertEqual(len(report.waves), 1)
        self.assertEqual(report.waves[0].reason, "serialized due to uncertainty or item policy")

    def test_workspace_checked_write_detects_lost_update(self) -> None:
        impl = RegistryTool(
            self.backend,
            self.acl,
            Actor(agent_id="impl", role=AgentRole.IMPLEMENTER, team_id="domain"),
        )
        first = impl.write_workspace_text_checked(
            "domain",
            "src/value.py",
            "VALUE = 1\n",
            expected_sha256="",
        )
        second = impl.write_workspace_text_checked(
            "domain",
            "src/value.py",
            "VALUE = 2\n",
            expected_sha256="",
        )

        self.assertFalse(first.conflict)
        self.assertTrue(second.conflict)
        self.assertEqual(second.status, "conflict")

    def test_failed_validation_evidence_rejects_submit_evidence(self) -> None:
        self.tool.write_team_contract(
            TeamContract(
                team_id="domain",
                work_items=[
                    TeamWorkItem(
                        work_id="domain:t1",
                        team_id="domain",
                        source_task_id="t1",
                        title="write",
                        goal="write code",
                    )
                ],
            )
        )
        failed = ValidationEvidence.new(
            team_id="domain",
            work_id="domain:t1",
            command="pytest",
            passed=False,
            exit_code=1,
            stderr="failed",
        )
        self.tool.append_validation_evidence(failed)
        op = ContractOperation.new(
            kind=OperationKind.SUBMIT_EVIDENCE,
            from_team="domain",
            target_ref="work:domain:t1",
            related_task_ids=["t1"],
            evidence_refs=[failed.ref()],
        )

        result = self.reducer.process(op)

        self.assertFalse(result.accepted)
        self.assertIn("did not pass", "; ".join(result.reasons))

    def test_implementer_rejects_artifact_outside_declared_writes(self) -> None:
        impl = RegistryTool(
            self.backend,
            self.acl,
            Actor(agent_id="impl", role=AgentRole.IMPLEMENTER, team_id="domain"),
        )
        ctx = BoundedContext("domain", "domain", ["code"], ["*"], "/workspace/domain/")
        task = TaskItem(
            task_id="t1",
            title="write allowed file",
            goal="write only allowed file",
            output_format="python",
        )
        work = TeamWorkItem.from_task_item("domain", task)
        work.writes = ["src/allowed.py"]
        packet = ContextPacket(
            plan=ProjectContract(intent=IntentLedger(goal="ship"), bounded_contexts=[ctx], frozen=True).to_plan(),
            bounded_context=ctx,
            subcontract=TeamSubContract.empty("domain"),
            task=work,
            work_item=work,
        )
        llm = NullLLMPort(
            fixed_text=(
                '{"artifacts": [{"path": "src/blocked.py", '
                '"content": "VALUE = 1\\n", "intent": "blocked"}], '
                '"decisions": [], "uncertainty": 0.0}'
            )
        )

        ImplementerPass(tool=impl, llm=llm).run(packet)

        self.assertTrue(any("outside declared writes" in b for b in packet.blockers))
        self.assertIsNone(impl.read_workspace_text("domain", "src/blocked.py"))
        self.assertIsNotNone(packet.change_set)
        assert packet.change_set is not None
        self.assertTrue(packet.change_set.has_conflicts())

    def test_judge_rejects_missing_declared_artifacts(self) -> None:
        item = TeamWorkItem(
            work_id="domain:t1",
            team_id="domain",
            source_task_id="t1",
            title="write",
            goal="write two files",
            writes=["src/a.py", "src/b.py"],
        )
        plan = self._project().to_plan()
        packet = ContextPacket(
            plan=plan,
            bounded_context=plan.context_of("domain"),
            subcontract=TeamSubContract.empty("domain"),
            task=item,
            work_item=item,
            artifacts=[
                SliceArtifact(
                    path="src/a.py",
                    content="A = 1\n",
                    intent="partial",
                )
            ],
        )

        judge_tool = RegistryTool(
            self.backend,
            self.acl,
            Actor(agent_id="judge", role=AgentRole.JUDGE, team_id="domain"),
        )
        JudgePass(judge_tool).run(packet, smoke_passed=True)

        self.assertIsNotNone(packet.verdict)
        assert packet.verdict is not None
        self.assertFalse(packet.verdict.approved)
        self.assertTrue(
            any("missing declared artifacts: src/b.py" in b for b in packet.verdict.blockers)
        )


if __name__ == "__main__":
    unittest.main()
