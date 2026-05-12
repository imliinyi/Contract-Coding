import json
import os
import tempfile
import unittest

from ContractCoding.config import Config
from ContractCoding.contract.compiler import ContractCompiler
from ContractCoding.contract.spec import ContractSpec, FeatureSlice, ProductKernel, RepairTransaction, WorkItem
from ContractCoding.knowledge.manager import ContextManager
from ContractCoding.quality.gates import RepairJudge
from ContractCoding.quality.transaction import QualityTransactionRunner
from ContractCoding.runtime.team import TeamRuntime
from ContractCoding.runtime.engine import RunEngine
from ContractCoding.runtime.repair import PatchGuard
from ContractCoding.runtime.scheduler import Scheduler
from ContractCoding.runtime.store import RunRecord
from ContractCoding.runtime.worker import DeterministicWorker, OpenAIWorker, WorkerResult


class RuntimeV5Tests(unittest.TestCase):
    def test_compiler_builds_product_kernel_and_feature_slices(self):
        task = (
            "Build package named atlas_ops with atlas_ops/__init__.py "
            "atlas_ops/domain/models.py atlas_ops/core/engine.py "
            "atlas_ops/io/storage.py atlas_ops/interface/cli.py tests/test_integration.py"
        )
        contract = ContractCompiler().compile(task)

        self.assertEqual(contract.product_kernel.status, "FROZEN")
        self.assertIn("artifact_coverage", [row["id"] for row in contract.product_kernel.acceptance_matrix])
        self.assertEqual(
            [feature_slice.id for feature_slice in contract.feature_slices],
            [
                "package_surface",
                "domain_foundation",
                "behavior_engine",
                "persistence_flow",
                "public_interface",
                "kernel_acceptance",
            ],
        )
        self.assertEqual(
            contract.slice_by_id()["public_interface"].dependencies,
            ["package_surface", "domain_foundation", "behavior_engine", "persistence_flow"],
        )
        capsule_items = [item for item in contract.work_items if item.kind == "capsule"]
        self.assertEqual(len(capsule_items), 5)
        self.assertEqual(len(contract.work_items), 11)
        self.assertEqual(capsule_items[0].phase, "team.capsule")
        self.assertTrue(all(capsule.status == "INTENT" for capsule in contract.interface_capsules if capsule.team_id != "kernel_acceptance"))
        behavior_item = next(item for item in contract.work_items if item.slice_id == "behavior_engine")
        self.assertIn("capsule:core_engine", behavior_item.dependencies)
        self.assertTrue(contract.team_subcontracts)
        self.assertEqual(contract.quality_transactions[0].scope, "plan")
        self.assertEqual(contract.quality_transactions[0].verdict, "APPROVE")
        self.assertIn("code_test_slice", contract.teams[0].agents[0].skills)
        acceptance_team = next(team for team in contract.teams if team.slice_id == "kernel_acceptance")
        self.assertIn("judge_contract_verification", acceptance_team.agents[0].skills)

    def test_progressive_skills_cover_plan_code_test_judge_repair_replan(self):
        manager = ContextManager()

        self.assertIn("planning_product_kernel", [skill["name"] for skill in manager.skills_for("plan")])
        self.assertIn("feature_slice_design", [skill["name"] for skill in manager.skills_for("plan")])
        self.assertIn("interface_contract_authoring", [skill["name"] for skill in manager.skills_for("plan")])
        self.assertIn("interface_capsule_handshake", [skill["name"] for skill in manager.skills_for("capsule")])
        self.assertIn("code_generation_slice", [skill["name"] for skill in manager.skills_for("worker")])
        self.assertIn("dependency_interface_consumption", [skill["name"] for skill in manager.skills_for("worker")])
        self.assertIn("tool_use_protocol", [skill["name"] for skill in manager.skills_for("worker")])
        self.assertIn("code_test_slice", [skill["name"] for skill in manager.skills_for("acceptance")])
        self.assertIn("acceptance_test_authoring", [skill["name"] for skill in manager.skills_for("acceptance")])
        self.assertIn("judge_contract_verification", [skill["name"] for skill in manager.skills_for("judge")])
        self.assertIn("repair_transaction", [skill["name"] for skill in manager.skills_for("repair")])
        self.assertIn("replan_failure_cluster", [skill["name"] for skill in manager.skills_for("replan")])
        skill_by_name = {skill["name"]: skill for skill in manager.skills_for("worker")}
        self.assertTrue(skill_by_name["code_generation_slice"]["source_path"].endswith("code_generation_slice/SKILL.md"))
        self.assertIn("Import canonical value objects", " ".join(skill_by_name["code_generation_slice"]["checklist"]))

    def test_openai_worker_packet_pushes_interface_responsibility_to_agent(self):
        contract = ContractCompiler().compile(
            "Build package named atlas_ops with atlas_ops/__init__.py "
            "atlas_ops/domain/models.py atlas_ops/core/engine.py "
            "atlas_ops/io/storage.py atlas_ops/interface/cli.py tests/test_integration.py"
        )
        item = next(work_item for work_item in contract.work_items if work_item.slice_id == "public_interface")
        feature_slice = contract.slice_by_id()[item.slice_id]
        worker = OpenAIWorker.__new__(OpenAIWorker)

        messages = worker._messages(contract, item, feature_slice, ContextManager().skills_for_item(item, feature_slice))
        payload = json.loads(messages[1]["content"])
        skill_names = [skill["name"] for skill in payload["progressive_skills"]]

        self.assertIn("runtime only orchestrates", messages[0]["content"])
        self.assertIn("dependency_interface_consumption", skill_names)
        self.assertIn("interface_contract_authoring", skill_names)
        self.assertIn("workflow_contract", payload)
        self.assertIn("agent_responsibility", payload["workflow_contract"])
        self.assertIn("agent_team_flow", payload["workflow_contract"])
        self.assertGreaterEqual(len(payload["team_slice_contracts"]), 1)
        self.assertGreaterEqual(len(payload["dependency_interface_capsules"]), 1)
        self.assertIn("team_subcontract", payload)
        self.assertIn("team_interface_capsule", payload)
        self.assertIn("required_preflight", payload)
        self.assertEqual(payload["context_policy"]["mode"], "bounded_feature_slice_packet")
        self.assertTrue(all("checklist" not in skill for skill in payload["progressive_skills"]))
        self.assertTrue(all("must" in skill and "avoid" in skill for skill in payload["progressive_skills"]))
        self.assertNotIn("teams", payload)

    def test_contract_tools_expose_contract_api_and_public_flow(self):
        from ContractCoding.tools.contract_tool import build_contract_tools

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("class PublicThing:\n    def run(self):\n        return 'ok'\n")
            contract = ContractSpec(
                goal="tool contract",
                product_kernel=ProductKernel(
                    ontology={"canonical_type_owners": {"PublicThing": "pkg/__init__.py"}},
                    flows=[
                        {
                            "id": "basic_public_flow",
                            "kind": "python_behavior_probe",
                            "code": "import pkg\nassert pkg.PublicThing().run() == 'ok'\n",
                        }
                    ],
                ),
                feature_slices=[FeatureSlice(id="package_surface", title="package", owner_artifacts=["pkg/__init__.py"])],
                work_items=[],
                required_artifacts=["pkg/__init__.py"],
            )
            item = WorkItem(id="slice:package_surface", slice_id="package_surface", title="package", allowed_artifacts=["pkg/__init__.py"])
            feature_slice = contract.feature_slices[0]
            tools = {tool.openai_schema["function"]["name"]: tool for tool in build_contract_tools(tmpdir, contract, item, feature_slice)}

            snapshot = json.loads(tools["contract_snapshot"]("current"))
            api = json.loads(tools["inspect_module_api"]("pkg"))
            flow = json.loads(tools["run_public_flow"]("basic_public_flow"))

            self.assertEqual(snapshot["canonical_types"]["PublicThing"], "pkg/__init__.py")
            self.assertEqual(api["classes"][0]["name"], "PublicThing")
            self.assertTrue(flow["ok"])

    def test_large_worker_packet_is_strictly_bounded(self):
        artifacts = [
            "aether_ops/__init__.py",
            "aether_ops/domain/__init__.py",
            "aether_ops/core/__init__.py",
            "aether_ops/core/engine.py",
            "aether_ops/interface/cli.py",
        ]
        artifacts += [f"aether_ops/domain/model_{idx}.py" for idx in range(14)]
        artifacts += [f"aether_ops/core/system_{idx}.py" for idx in range(12)]
        artifacts += [f"aether_ops/planning/policy_{idx}.py" for idx in range(8)]
        artifacts += [f"aether_ops/io/storage_{idx}.py" for idx in range(8)]
        artifacts += [f"tests/test_generated_{idx}.py" for idx in range(8)]
        contract = ContractCompiler().compile("Build a very large package with " + " ".join(artifacts))
        item = next(work_item for work_item in contract.work_items if work_item.slice_id.startswith("core_"))
        feature_slice = contract.slice_by_id()[item.slice_id]
        worker = OpenAIWorker.__new__(OpenAIWorker)

        messages = worker._messages(contract, item, feature_slice, ContextManager().skills_for_item(item, feature_slice))
        payload = json.loads(messages[1]["content"])

        self.assertLess(len(messages[1]["content"]), 16000)
        self.assertEqual(payload["context_policy"]["mode"], "bounded_feature_slice_packet")
        self.assertLessEqual(len(payload["dependency_interface_capsules"]), 16)
        self.assertTrue(all("checklist" not in skill for skill in payload["progressive_skills"]))
        self.assertTrue(all(len(skill.get("must", [])) <= 3 for skill in payload["progressive_skills"]))
        self.assertTrue(all(len(skill.get("avoid", [])) <= 2 for skill in payload["progressive_skills"]))
        self.assertNotIn("feature_slices", payload)
        self.assertNotIn("teams", payload)

    def test_compiler_materializes_executable_slice_contracts(self):
        contract = ContractCompiler().compile(
            "Build a route navigation package named route_ops with at least 10000 meaningful non-empty lines "
            "route_ops/__init__.py route_ops/domain/routes.py route_ops/core/navigation.py "
            "route_ops/io/scenarios.py route_ops/interface/cli.py tests/test_routes.py"
        )

        semantic_ids = [invariant["id"] for invariant in contract.product_kernel.semantic_invariants]
        self.assertIn("meaningful_scale_budget", semantic_ids)
        self.assertIn("route_fixture_distance_consistency", semantic_ids)
        self.assertIn("GeoPoint", contract.product_kernel.ontology["value_objects"])
        self.assertIn("GridPoint", contract.product_kernel.ontology["value_objects"])
        self.assertEqual(contract.product_kernel.test_generation_policy["mode"], "kernel_derived")
        self.assertIn("slice_smoke", [row["id"] for row in contract.product_kernel.acceptance_matrix])
        behavior = contract.slice_by_id()["behavior_engine"]
        self.assertTrue(behavior.interface_contract["size_budget"]["enabled"])
        self.assertIn("forbidden_equivalences", behavior.semantic_contract)
        self.assertGreater(behavior.interface_contract["size_budget"]["min_total_loc"], 0)
        self.assertEqual(behavior.slice_smoke[0]["kind"], "python_import")
        self.assertIn("route_ops.core.navigation", behavior.slice_smoke[0]["modules"])

    def test_slice_judge_records_size_budget_without_blocking_promotion(self):
        from ContractCoding.quality.gates import SliceJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            feature_slice = FeatureSlice(
                id="package_surface",
                title="package",
                owner_artifacts=["pkg/__init__.py"],
                interface_contract={
                    "owner_artifacts": ["pkg/__init__.py"],
                    "public_modules": ["pkg"],
                    "size_budget": {"enabled": True, "min_total_loc": 5},
                },
                slice_smoke=[{"id": "import", "kind": "python_import", "modules": ["pkg"]}],
            )

            result = SliceJudge(tmpdir).check(feature_slice)

            self.assertTrue(result.ok)
            self.assertIn("slice_smoke_import:pkg", result.evidence)
            self.assertIn("slice_size_budget_warning:package_surface:1/5", result.evidence)
            self.assertEqual(result.diagnostics, [])

    def test_quality_transaction_approves_only_after_test_and_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")
            contract = ContractSpec(
                goal="quality transaction",
                product_kernel=ProductKernel(acceptance_matrix=[{"id": "artifact_coverage"}]),
                feature_slices=[
                    FeatureSlice(
                        id="package_surface",
                        title="package",
                        owner_artifacts=["pkg/__init__.py"],
                        interface_contract={"owner_artifacts": ["pkg/__init__.py"], "public_modules": ["pkg"]},
                    )
                ],
                work_items=[],
                required_artifacts=["pkg/__init__.py"],
            )
            item = WorkItem(
                id="item:package_surface",
                slice_id="package_surface",
                title="package",
                allowed_artifacts=["pkg/__init__.py"],
            )
            worker_result = WorkerResult(ok=True, changed_files=["pkg/__init__.py"], evidence=["wrote:pkg/__init__.py"])

            result = QualityTransactionRunner(tmpdir).check_item(
                "run-quality",
                contract,
                item,
                contract.feature_slices[0],
                worker_result,
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.record.verdict, "APPROVE")
            self.assertIn("quality_review:approved", result.record.review_evidence)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding/quality/run-quality/item_package_surface.json")))
            self.assertEqual(len(contract.quality_transactions), 1)

    def test_quality_review_blocks_unowned_worker_claim_before_promotion(self):
        class UnownedClaimWorker:
            def execute(self, workspace_dir, contract, item):
                path = os.path.join(workspace_dir, "pkg", "__init__.py")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("VALUE = 1\n")
                return WorkerResult(
                    ok=True,
                    changed_files=["pkg/__init__.py", "pkg/other.py"],
                    evidence=["claimed extra file"],
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractSpec(
                goal="quality review blocks unowned",
                product_kernel=ProductKernel(acceptance_matrix=[{"id": "artifact_coverage"}]),
                feature_slices=[
                    FeatureSlice(
                        id="package_surface",
                        title="package",
                        owner_artifacts=["pkg/__init__.py"],
                        interface_contract={"owner_artifacts": ["pkg/__init__.py"], "public_modules": ["pkg"]},
                    )
                ],
                work_items=[],
                required_artifacts=["pkg/__init__.py"],
            )
            item = WorkItem(
                id="item:package_surface",
                slice_id="package_surface",
                title="package",
                allowed_artifacts=["pkg/__init__.py"],
            )

            result = TeamRuntime(tmpdir).execute("run-review", contract, item, UnownedClaimWorker())

            self.assertFalse(result.ok)
            self.assertIn("review_unowned_reported_change", [diag["code"] for diag in result.diagnostics])
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "pkg", "__init__.py")))
            self.assertEqual(contract.quality_transactions[0].status, "REJECTED")

    def test_large_project_uses_granular_parallel_feature_slices(self):
        artifacts = [
            "path_ops/__init__.py",
            "path_ops/domain/models.py",
            "path_ops/domain/maps.py",
            "path_ops/domain/fleet.py",
            "path_ops/domain/shipments.py",
            "path_ops/core/routing.py",
            "path_ops/core/dispatch.py",
            "path_ops/core/simulation.py",
            "path_ops/planning/optimizer.py",
            "path_ops/io/scenarios.py",
            "path_ops/io/storage.py",
            "path_ops/interface/cli.py",
            "path_ops/utils/validation.py",
            "tests/test_integration.py",
        ]
        contract = ContractCompiler().compile("Build a large package with " + " ".join(artifacts))
        slice_ids = [feature_slice.id for feature_slice in contract.feature_slices]

        self.assertIn("domain_models", slice_ids)
        self.assertIn("domain_maps", slice_ids)
        self.assertIn("core_routing", slice_ids)
        self.assertGreaterEqual(len(contract.feature_slices), 12)

        scheduler = Scheduler()
        first = scheduler.ready_items(contract, limit=8)
        first_ids = [item.slice_id for item in first]
        self.assertIn("capsule:foundation", first_ids)
        self.assertIn("capsule:canonical_substrate", first_ids)
        self.assertIn("capsule:domain_kernel", first_ids)
        self.assertTrue(all(item.kind == "capsule" for item in first))
        for item in first:
            item.status = "VERIFIED"

        second_waves = scheduler.ready_team_waves(contract, max_teams=4, max_items_per_team=8)
        self.assertEqual(second_waves[0].feature_team_id, "foundation")
        self.assertFalse(second_waves[0].internal_parallel)
        second_waves[0].items[0].status = "VERIFIED"

        third_waves = scheduler.ready_team_waves(contract, max_teams=4, max_items_per_team=8)
        self.assertEqual(third_waves[0].feature_team_id, "canonical_substrate")
        self.assertEqual([item.slice_id for item in third_waves[0].items], ["domain_models"])
        third_waves[0].items[0].status = "VERIFIED"

        fourth_waves = scheduler.ready_team_waves(contract, max_teams=4, max_items_per_team=8)
        if fourth_waves and fourth_waves[0].feature_team_id == "canonical_substrate":
            for item in fourth_waves[0].items:
                item.status = "VERIFIED"
            fourth_waves = scheduler.ready_team_waves(contract, max_teams=4, max_items_per_team=8)
        self.assertEqual(fourth_waves[0].feature_team_id, "domain_kernel")
        self.assertTrue(fourth_waves[0].internal_parallel)
        second = [item for wave in fourth_waves for item in wave.items]
        second_ids = [item.slice_id for item in second]
        self.assertIn("domain_maps", second_ids)
        self.assertIn("utility_validation", second_ids)
        self.assertGreaterEqual(len(second_ids), 3)

    def test_canonical_substrate_becomes_first_class_team_contract(self):
        artifacts = [
            "path_ops/__init__.py",
            "path_ops/domain/models.py",
            "path_ops/domain/maps.py",
            "path_ops/domain/tasks.py",
            "path_ops/domain/fleet.py",
            "path_ops/domain/resources.py",
            "path_ops/domain/invariants.py",
            "path_ops/core/dispatch.py",
            "path_ops/core/routing.py",
            "path_ops/core/simulation.py",
            "path_ops/planning/optimizer.py",
            "path_ops/interface/cli.py",
            "path_ops/io/storage.py",
            "tests/test_integration.py",
        ]
        contract = ContractCompiler().compile(
            "Build a routing and dispatch package with grid coordinates, resources, scenarios, and "
            + " ".join(artifacts)
        )

        self.assertEqual(
            contract.canonical_substrate.substrate_slice_ids,
            ["domain_models", "domain_tasks", "domain_fleet"],
        )
        self.assertIn("domain_models", contract.slice_by_id()["domain_maps"].dependencies)
        self.assertIn("domain_models", contract.slice_by_id()["domain_tasks"].dependencies)
        self.assertIn("domain_models", contract.slice_by_id()["domain_fleet"].dependencies)
        canonical = contract.team_subcontract_by_team_id()["canonical_substrate"]
        self.assertIn("GridPoint", canonical.owned_concepts)
        domain_capsule = contract.interface_capsule_by_team_id()["domain_kernel"]
        self.assertIn("GridPoint", domain_capsule.canonical_imports)
        self.assertEqual(contract.slice_by_id()["domain_models"].phase, "kernel.substrate")

    def test_slice_judge_blocks_canonical_redefinition_before_final_gate(self):
        from ContractCoding.quality.gates import SliceJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "domain"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "domain", "tasks.py"), "w", encoding="utf-8") as handle:
                handle.write("class GridPoint:\n    pass\n")
            contract = ContractSpec(
                goal="canonical slice",
                product_kernel=ProductKernel(
                    ontology={"canonical_type_owners": {"GridPoint": "pkg/domain/models.py"}},
                    acceptance_matrix=[{"id": "canonical_type_ownership"}],
                ),
                feature_slices=[
                    FeatureSlice(id="tasks", title="tasks", owner_artifacts=["pkg/domain/tasks.py"]),
                ],
                work_items=[],
                required_artifacts=["pkg/domain/tasks.py"],
            )

            result = SliceJudge(tmpdir).check(contract.feature_slices[0], contract)

            self.assertFalse(result.ok)
            self.assertIn("canonical_type_redefined", [diag["code"] for diag in result.diagnostics])

    def test_openai_quality_review_requires_context_preflight(self):
        from ContractCoding.quality.gates import GateResult
        from ContractCoding.quality.transaction import QualityReviewJudge

        contract = ContractSpec(
            goal="preflight",
            product_kernel=ProductKernel(acceptance_matrix=[{"id": "producer_consumer_shape"}]),
            feature_slices=[FeatureSlice(id="core", title="core", owner_artifacts=["pkg/core.py"], dependencies=["models"])],
            work_items=[],
            required_artifacts=["pkg/core.py"],
        )
        item = WorkItem(
            id="slice:core",
            slice_id="core",
            title="core",
            allowed_artifacts=["pkg/core.py"],
            dependencies=["models"],
            kind="implementation",
        )
        worker_result = WorkerResult(
            ok=True,
            changed_files=["pkg/core.py"],
            evidence=["compile:pkg/core.py"],
            raw={"backend": "openai", "raw": {"tool_results": [{"name": "submit_result"}]}},
        )

        review = QualityReviewJudge().review(
            scope="slice",
            contract=contract,
            item=item,
            feature_slice=contract.feature_slices[0],
            worker_result=worker_result,
            test_result=GateResult(ok=True, evidence=["compile:pkg/core.py"]),
        )

        self.assertFalse(review.ok)
        codes = [diag["code"] for diag in review.diagnostics]
        self.assertIn("review_missing_contract_snapshot_preflight", codes)
        self.assertIn("review_missing_dependency_api_preflight", codes)

    def test_repair_validation_blocks_promotion_before_main_workspace_changes(self):
        class BadRepairWorker:
            def execute(self, workspace_dir, contract, item):
                path = os.path.join(workspace_dir, "pkg", "core.py")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("def value():\n    return 3\n")
                return WorkerResult(ok=True, changed_files=["pkg/core.py"], evidence=["bad patch"])

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "core.py"), "w", encoding="utf-8") as handle:
                handle.write("def value():\n    return 1\n")
            with open(os.path.join(tmpdir, "tests", "test_core.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    "import unittest\n"
                    "from pkg import core\n\n"
                    "class CoreTests(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    "        self.assertEqual(core.value(), 2)\n"
                )
            contract = ContractSpec(
                goal="repair validation",
                product_kernel=ProductKernel(acceptance_matrix=[{"id": "declared_tests_pass"}]),
                feature_slices=[
                    FeatureSlice(
                        id="core",
                        title="core",
                        owner_artifacts=["pkg/core.py"],
                    )
                ],
                work_items=[],
                required_artifacts=["pkg/core.py", "tests/test_core.py"],
                test_artifacts=["tests/test_core.py"],
                repair_transactions=[
                    RepairTransaction(
                        id="repair:bad",
                        failure_fingerprint="bad",
                        root_invariant="tests_compile_kernel_acceptance",
                        allowed_artifacts=["pkg/core.py"],
                        locked_tests=["tests/test_core.py"],
                        validation_commands=[["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"]],
                    )
                ],
            )
            item = WorkItem(
                id="repair:bad:attempt:1",
                slice_id="repair:bad",
                title="repair",
                allowed_artifacts=["pkg/core.py"],
                kind="repair",
                team_id="team:repair",
                locked_artifacts=["tests/test_core.py"],
                repair_transaction_id="repair:bad",
            )

            result = TeamRuntime(tmpdir).execute("run1", contract, item, BadRepairWorker())

            self.assertFalse(result.ok)
            self.assertIn("repair_validation_failed", [diag["code"] for diag in result.diagnostics])
            with open(os.path.join(tmpdir, "pkg", "core.py"), "r", encoding="utf-8") as handle:
                self.assertIn("return 1", handle.read())
            self.assertEqual(contract.repair_transactions[0].last_validation["ok"], False)

    def test_patch_guard_rolls_back_syntax_breaking_python_write(self):
        class Intent:
            name = "replace_file"
            arguments = {"path": "pkg/mod.py"}

        class Result:
            allowed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "pkg", "mod.py")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            guard = PatchGuard(tmpdir, allowed_artifacts=["pkg/mod.py"])
            guard.before_tool(Intent())
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("def broken(:\n")
            result = guard.after_tool(Intent(), Result())

            self.assertTrue(result.rolled_back)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "VALUE = 1\n")

    def test_openai_auto_submit_requires_all_artifacts_to_compile(self):
        from ContractCoding.llm.openai_backend import OpenAIBackend

        with tempfile.TemporaryDirectory() as tmpdir:
            good = os.path.join(tmpdir, "tests", "test_good.py")
            bad = os.path.join(tmpdir, "tests", "test_bad.py")
            os.makedirs(os.path.dirname(good), exist_ok=True)
            with open(good, "w", encoding="utf-8") as handle:
                handle.write("def test_good():\n    assert True\n")
            with open(bad, "w", encoding="utf-8") as handle:
                handle.write("def broken(:\n")
            backend = OpenAIBackend.__new__(OpenAIBackend)
            backend.workspace_dir = tmpdir
            backend.allowed_artifacts = ["tests/test_good.py", "tests/test_bad.py"]

            self.assertEqual(backend._auto_submit_allowed_artifacts(), {})

            with open(bad, "w", encoding="utf-8") as handle:
                handle.write("def test_bad():\n    assert True\n")
            result = backend._auto_submit_allowed_artifacts()

            self.assertEqual(result["tool_name"], "submit_result")
            self.assertIn("owner_artifacts_compile:pass", result["evidence"])

    def test_resume_reopens_retryable_local_slice_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(Config(WORKSPACE_DIR=tmpdir), worker=DeterministicWorker())
            contract = ContractCompiler().compile("Build package named retry_ops with retry_ops/__init__.py")
            item = contract.work_items[0]
            item.status = "BLOCKED"
            item.attempts = 0
            item.diagnostics = [{"code": "syntax_error", "artifact": "retry_ops/__init__.py"}]
            run = engine.store.create_run("retry local syntax failure", contract)
            run.status = "BLOCKED"
            engine.store.save(run)

            result = engine.resume(run.id, max_steps=1, offline=True)
            saved = engine.store.get(run.id)

            self.assertNotEqual(result.status, "BLOCKED")
            self.assertEqual(saved.contract.work_items[0].status, "VERIFIED")
            self.assertTrue(any(event["type"] == "local_retry_reopened" for event in engine.events(run.id, limit=20)))

    def test_scheduler_runs_independent_slices_after_dependencies(self):
        contract = ContractCompiler().compile(
            "Build x/__init__.py x/domain/models.py x/core/engine.py x/io/storage.py x/interface/cli.py"
        )
        scheduler = Scheduler()
        ready = scheduler.ready_items(contract, limit=8)
        self.assertEqual(
            [item.slice_id for item in ready],
            [
                "capsule:foundation",
                "capsule:domain_kernel",
                "capsule:core_engine",
                "capsule:scenario_persistence",
                "capsule:public_interface",
            ],
        )
        for item in ready:
            item.status = "VERIFIED"

        next_ready = scheduler.ready_items(contract, limit=4)
        self.assertEqual([item.slice_id for item in next_ready], ["package_surface"])
        next_ready[0].status = "VERIFIED"

        domain_ready = scheduler.ready_items(contract, limit=4)
        self.assertEqual([item.slice_id for item in domain_ready], ["domain_foundation"])

    def test_offline_run_completes_and_writes_kernel_monitor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(Config(WORKSPACE_DIR=tmpdir), worker=DeterministicWorker())
            result = engine.run(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/domain/models.py atlas_ops/core/engine.py "
                "atlas_ops/io/storage.py atlas_ops/interface/cli.py tests/test_integration.py",
                max_steps=20,
                offline=True,
            )

            self.assertEqual(result.status, "COMPLETED")
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding/kernel/product_kernel.json")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, ".contractcoding/slices/behavior_engine.json")))
            snapshot = engine.status(result.run_id)
            self.assertEqual(snapshot["run"]["status"], "COMPLETED")
            self.assertEqual(snapshot["kernel"]["status"], "FROZEN")
            self.assertEqual(len(snapshot["teams"]), 6)
            self.assertEqual(len(snapshot["promotions"]), 6)
            self.assertEqual(len(snapshot["quality_transactions"]), 13)
            self.assertTrue(any(record["scope"] == "plan" for record in snapshot["quality_transactions"]))
            self.assertTrue(all(capsule["status"] == "LOCKED" for capsule in snapshot["interface_capsules"] if capsule["team_id"] != "kernel_acceptance"))
            self.assertEqual(len(snapshot["repair_transactions"]), 0)
            promotion_dir = os.path.join(tmpdir, ".contractcoding", "promotions", result.run_id)
            self.assertTrue(os.path.exists(os.path.join(promotion_dir, "behavior_engine.json")))

    def test_acceptance_is_kernel_derived_not_worker_authored(self):
        class BrokenTestsWorker(DeterministicWorker):
            def _content_for(self, artifact, contract):
                if artifact.startswith("tests/"):
                    return (
                        "import unittest\n\n"
                        "class Broken(unittest.TestCase):\n"
                        "    def test_failure(self):\n"
                        "        self.assertEqual(1, 2)\n"
                    )
                return super()._content_for(artifact, contract)

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(Config(WORKSPACE_DIR=tmpdir), worker=BrokenTestsWorker())
            result = engine.run(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/core/engine.py tests/test_integration.py",
                max_steps=10,
                offline=True,
            )

            self.assertEqual(result.status, "COMPLETED")
            snapshot = engine.status(result.run_id)
            self.assertEqual(len(snapshot["repair_transactions"]), 0)
            with open(os.path.join(tmpdir, "tests/test_integration.py"), "r", encoding="utf-8") as handle:
                generated = handle.read()
            self.assertIn("Kernel-derived acceptance tests", generated)
            self.assertNotIn("self.assertEqual(1, 2)", generated)

    def test_acceptance_dependency_blocker_opens_central_repair_and_retries_slice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/core/engine.py tests/test_integration.py"
            )
            run = RunRecord(
                id="run-blocker",
                task="task",
                workspace=tmpdir,
                status="RUNNING",
                contract=contract,
            )
            item = next(candidate for candidate in contract.work_items if candidate.kind == "acceptance")
            diagnostic = {
                "tool_name": "report_blocker",
                "blocker_type": "missing_interface",
                "reason": "atlas_ops/core/engine.py exposes an incompatible producer contract",
                "required_artifacts": ["atlas_ops/core/engine.py"],
            }

            handled = RunEngine(Config(WORKSPACE_DIR=tmpdir)).recovery.handle_item_blocker(run, item, [diagnostic])

            self.assertTrue(handled)
            self.assertEqual(item.status, "PENDING")
            self.assertEqual(item.diagnostics[0]["code"], "waiting_on_repair_transaction")
            repair_items = [candidate for candidate in contract.work_items if candidate.kind == "repair"]
            self.assertEqual(len(repair_items), 1)
            self.assertIn(repair_items[0].id, item.dependencies)
            transaction = contract.repair_transactions[0]
            self.assertEqual(transaction.allowed_artifacts, ["atlas_ops/core/engine.py"])
            self.assertEqual(transaction.validation_commands, [["{python}", "-m", "compileall", "."]])

    def test_final_failure_scopes_production_artifacts_when_diagnostic_points_at_test(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "tests/test_integration.py"), "w", encoding="utf-8") as handle:
                handle.write("import unittest\n")
            contract = ContractCompiler().compile(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/core/engine.py tests/test_integration.py"
            )
            run = RunRecord(
                id="run-final-scope",
                task="task",
                workspace=tmpdir,
                status="RUNNING",
                contract=contract,
            )
            diagnostics = [
                {
                    "code": "python_test_function_failure",
                    "artifact": "tests/test_integration.py",
                    "kernel_invariant": "tests_compile_kernel_acceptance",
                    "message": "test_flow: ValueError: producer behavior mismatch",
                }
            ]

            handled = RunEngine(Config(WORKSPACE_DIR=tmpdir)).recovery.handle_final_failure(run, diagnostics)

            self.assertTrue(handled)
            transaction = contract.repair_transactions[0]
            self.assertTrue(transaction.allowed_artifacts)
            self.assertNotIn("tests/test_integration.py", transaction.allowed_artifacts)
            self.assertIn("atlas_ops/core/engine.py", transaction.allowed_artifacts)

    def test_repair_scope_blocker_expands_transaction_allowed_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/core/engine.py atlas_ops/interface/cli.py tests/test_integration.py"
            )
            transaction = RepairTransaction(
                id="repair:empty",
                failure_fingerprint="empty",
                root_invariant="tests_compile_kernel_acceptance",
                locked_tests=["tests/test_integration.py"],
                allowed_artifacts=[],
                attempts=1,
            )
            contract.repair_transactions.append(transaction)
            repair_item = WorkItem(
                id="repair:empty:attempt:1",
                slice_id="repair:empty",
                title="repair",
                allowed_artifacts=[],
                kind="repair",
                phase="repair.transaction",
                team_id="team:repair",
                repair_transaction_id=transaction.id,
            )
            contract.work_items.append(repair_item)
            run = RunRecord(
                id="run-expand-scope",
                task="task",
                workspace=tmpdir,
                status="RUNNING",
                contract=contract,
            )
            diagnostics = [
                {
                    "tool_name": "report_blocker",
                    "blocker_type": "out_of_scope_repair",
                    "reason": "producer files must change",
                    "required_artifacts": ["atlas_ops/core/engine.py", "atlas_ops/interface/cli.py"],
                }
            ]

            continued = RunEngine(Config(WORKSPACE_DIR=tmpdir)).recovery.handle_repair_failure(run, repair_item, diagnostics)

            self.assertTrue(continued)
            self.assertEqual(repair_item.status, "SUPERSEDED")
            self.assertEqual(transaction.allowed_artifacts, ["atlas_ops/core/engine.py", "atlas_ops/interface/cli.py"])
            self.assertTrue(any(item.id == "repair:empty:attempt:2" for item in contract.work_items))

    def test_repair_judge_runs_locked_pytest_style_functions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            main = os.path.join(tmpdir, "main")
            team = os.path.join(tmpdir, "team")
            os.makedirs(os.path.join(main, "tests"), exist_ok=True)
            os.makedirs(os.path.join(team, "tests"), exist_ok=True)
            test_text = "def test_locked_behavior():\n    assert False\n"
            for root in (main, team):
                with open(os.path.join(root, "tests/test_behavior.py"), "w", encoding="utf-8") as handle:
                    handle.write(test_text)
            contract = ContractCompiler().compile(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/core.py tests/test_behavior.py"
            )
            transaction = RepairTransaction(
                id="repair:functions",
                failure_fingerprint="functions",
                root_invariant="tests_compile_kernel_acceptance",
                locked_tests=["tests/test_behavior.py"],
                validation_commands=[["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"]],
            )
            contract.repair_transactions.append(transaction)
            item = WorkItem(
                id="repair:functions:attempt:1",
                slice_id="repair:functions",
                title="repair",
                allowed_artifacts=["atlas_ops/core.py"],
                kind="repair",
                phase="repair.transaction",
                team_id="team:repair",
                repair_transaction_id=transaction.id,
            )

            result = RepairJudge(team, main).check(contract, item)

            self.assertFalse(result.ok)
            self.assertEqual(result.diagnostics[0]["code"], "python_test_function_failure")

    def test_semantic_final_failure_opens_kernel_replan_not_repair_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build route dispatch package named route_ops with route_ops/__init__.py "
                "route_ops/domain/geo.py route_ops/domain/facilities.py route_ops/domain/tasks.py "
                "route_ops/core/routing.py route_ops/core/dispatch.py tests/test_routing.py"
            )
            run = RunRecord(
                id="run-semantic",
                task="task",
                workspace=tmpdir,
                status="RUNNING",
                contract=contract,
            )
            diagnostics = [
                {
                    "code": "forbidden_value_object_equivalence",
                    "artifact": "route_ops/core/routing.py",
                    "kernel_invariant": "semantic_ontology_consistency",
                    "message": "GeoPoint constructed directly from GridPoint x/y and then failed with latitude out of range: 906.0",
                }
            ]

            handled = RunEngine(Config(WORKSPACE_DIR=tmpdir)).recovery.handle_final_failure(run, diagnostics)

            self.assertTrue(handled)
            self.assertEqual(len(contract.repair_transactions), 0)
            self.assertEqual(len(contract.replans), 1)
            self.assertIn("ontology_patch", contract.replans[0].kernel_delta)
            self.assertTrue(set(contract.replans[0].affected_slices).intersection({"core_routing", "behavior_engine"}))
            self.assertTrue(contract.product_kernel.ontology.get("replan_patches"))

    def test_scheduler_ready_wave_skips_conflicting_items(self):
        contract = ContractSpec(
            goal="conflict test",
            product_kernel=ProductKernel(acceptance_matrix=[{"id": "artifact_coverage"}]),
            feature_slices=[
                FeatureSlice(id="a", title="a", owner_artifacts=["pkg/a.py"], conflict_keys=["artifact:pkg/shared.py"]),
                FeatureSlice(id="b", title="b", owner_artifacts=["pkg/b.py"], conflict_keys=["artifact:pkg/shared.py"]),
                FeatureSlice(id="c", title="c", owner_artifacts=["pkg/c.py"], conflict_keys=["artifact:pkg/c.py"]),
            ],
            work_items=[
                WorkItem(id="a", slice_id="a", title="a", allowed_artifacts=["pkg/a.py"], conflict_keys=["artifact:pkg/shared.py"]),
                WorkItem(id="b", slice_id="b", title="b", allowed_artifacts=["pkg/b.py"], conflict_keys=["artifact:pkg/shared.py"]),
                WorkItem(id="c", slice_id="c", title="c", allowed_artifacts=["pkg/c.py"], conflict_keys=["artifact:pkg/c.py"]),
            ],
            required_artifacts=["pkg/a.py", "pkg/b.py", "pkg/c.py"],
        )

        ready = Scheduler().ready_wave(contract, limit=3)

        self.assertEqual([item.id for item in ready], ["a", "c"])

    def test_monitor_includes_team_promotion_replan_and_telemetry_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(Config(WORKSPACE_DIR=tmpdir), worker=DeterministicWorker())
            result = engine.run(
                "Build package named atlas_ops with atlas_ops/__init__.py "
                "atlas_ops/core/engine.py tests/test_integration.py",
                max_steps=10,
                offline=True,
            )

            snapshot = engine.status(result.run_id)

            self.assertEqual(result.status, "COMPLETED")
            self.assertIn("ready_wave", snapshot)
            self.assertIn("ready_team_waves", snapshot)
            self.assertIn("teams", snapshot)
            self.assertIn("team_states", snapshot)
            self.assertIn("promotions", snapshot)
            self.assertIn("quality_transactions", snapshot)
            self.assertIn("replans", snapshot)
            self.assertIn("llm_telemetry", snapshot)
            self.assertTrue(any(record["scope"] == "integration" for record in snapshot["quality_transactions"]))
            self.assertNotIn("API_KEY", json.dumps(snapshot))

    def test_large_30_file_offline_e2e_uses_slice_teams_and_promotions(self):
        artifacts = [
            "mega_sim/__init__.py",
            "mega_sim/core/engine.py",
            "mega_sim/interface/cli.py",
        ]
        artifacts += [f"mega_sim/domain/model_{idx}.py" for idx in range(8)]
        artifacts += [f"mega_sim/core/system_{idx}.py" for idx in range(6)]
        artifacts += [f"mega_sim/planning/policy_{idx}.py" for idx in range(4)]
        artifacts += [f"mega_sim/ai/planner_{idx}.py" for idx in range(4)]
        artifacts += [f"mega_sim/io/storage_{idx}.py" for idx in range(4)]
        artifacts += [f"tests/test_generated_{idx}.py" for idx in range(4)]
        self.assertGreaterEqual(len(artifacts), 30)

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = RunEngine(Config(WORKSPACE_DIR=tmpdir, AUTO_MAX_STEWARD_LOOPS=80), worker=DeterministicWorker())
            result = engine.run("Build a large package with " + " ".join(artifacts), max_steps=80, offline=True)

            self.assertEqual(result.status, "COMPLETED")
            snapshot = engine.status(result.run_id)
            existing = [artifact for artifact in artifacts if os.path.exists(os.path.join(tmpdir, artifact))]
            self.assertEqual(len(existing), len(artifacts))
            self.assertGreaterEqual(len(snapshot["feature_teams"]), 7)
            self.assertTrue(any(len(team["slice_ids"]) > 1 for team in snapshot["feature_teams"]))
            self.assertTrue(all("team_contract" in team for team in snapshot["feature_teams"]))
            promotable_items = [item for item in snapshot["items"] if item["kind"] not in {"capsule", "interface"}]
            self.assertGreaterEqual(len(snapshot["promotions"]), len(promotable_items))
            self.assertTrue(any(item["kind"] == "capsule" for item in snapshot["items"]))
            self.assertEqual(len(snapshot["repair_transactions"]), 0)

    def test_final_gate_blocks_marked_temporary_mocks(self):
        from ContractCoding.quality.gates import IntegrationJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            with open(os.path.join(tmpdir, "pkg", "core.py"), "w", encoding="utf-8") as handle:
                handle.write("CONTRACTCODING_MOCK = {'mock_id': 'm1', 'real_owner_slice': 'core'}\n")
            contract = ContractSpec(
                goal="mock final gate",
                product_kernel=ProductKernel(acceptance_matrix=[{"id": "controlled_mock_lifecycle"}]),
                feature_slices=[FeatureSlice(id="core", title="core", owner_artifacts=["pkg/core.py"])],
                work_items=[],
                required_artifacts=["pkg/__init__.py", "pkg/core.py"],
            )

            result = IntegrationJudge(tmpdir).check(contract)

            self.assertFalse(result.ok)
            self.assertIn("unresolved_mock", [diag["code"] for diag in result.diagnostics])

    def test_final_gate_runs_pytest_style_test_functions_without_pytest(self):
        from ContractCoding.quality.gates import IntegrationJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("__version__ = '0.1.0'\n")
            with open(os.path.join(tmpdir, "tests", "test_behavior.py"), "w", encoding="utf-8") as handle:
                handle.write("def test_behavior():\n    assert False\n")
            contract = ContractSpec(
                goal="pytest style test failure",
                product_kernel=ProductKernel(acceptance_matrix=[{"id": "declared_tests_pass"}]),
                feature_slices=[FeatureSlice(id="package_surface", title="package", owner_artifacts=["pkg/__init__.py"])],
                work_items=[],
                required_artifacts=["pkg/__init__.py", "tests/test_behavior.py"],
                test_artifacts=["tests/test_behavior.py"],
            )

            result = IntegrationJudge(tmpdir).check(contract)

            self.assertFalse(result.ok)
            self.assertIn("python_test_function_failure", [diag["code"] for diag in result.diagnostics])

    def test_semantic_linter_blocks_gridpoint_geopoint_equivalence(self):
        from ContractCoding.quality.gates import IntegrationJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build route package named route_ops with route_ops/__init__.py "
                "route_ops/domain/geo.py route_ops/domain/facilities.py route_ops/core/routing.py tests/test_routing.py"
            )
            for artifact in contract.required_artifacts:
                path = os.path.join(tmpdir, artifact)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if artifact == "route_ops/core/routing.py":
                    content = (
                        "from route_ops.domain.geo import GeoPoint\n\n"
                        "def bad_route(facility):\n"
                        "    return GeoPoint(lat=float(facility.x), lon=float(facility.y))\n"
                    )
                elif artifact == "route_ops/domain/geo.py":
                    content = "class GeoPoint:\n    def __init__(self, lat, lon):\n        self.lat = lat\n        self.lon = lon\n"
                elif artifact.endswith("__init__.py"):
                    content = "__version__ = '0.1.0'\n"
                elif artifact.startswith("tests/"):
                    content = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
                else:
                    content = "class Facility:\n    x = 1\n    y = 2\n"
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(content)

            result = IntegrationJudge(tmpdir).check(contract)

            self.assertFalse(result.ok)
            self.assertIn("forbidden_value_object_equivalence", [diag["code"] for diag in result.diagnostics])

    def test_semantic_linter_blocks_canonical_type_redefinition(self):
        from ContractCoding.quality.gates import IntegrationJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg", "domain"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("__version__ = '0.1.0'\n")
            with open(os.path.join(tmpdir, "pkg", "domain", "models.py"), "w", encoding="utf-8") as handle:
                handle.write("class GridPoint:\n    pass\n")
            with open(os.path.join(tmpdir, "pkg", "domain", "tasks.py"), "w", encoding="utf-8") as handle:
                handle.write("class GridPoint:\n    pass\n")
            with open(os.path.join(tmpdir, "tests", "test_ok.py"), "w", encoding="utf-8") as handle:
                handle.write("import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
            contract = ContractSpec(
                goal="canonical ownership",
                product_kernel=ProductKernel(
                    ontology={"canonical_type_owners": {"GridPoint": "pkg/domain/models.py"}},
                    acceptance_matrix=[{"id": "canonical_type_ownership"}],
                ),
                feature_slices=[
                    FeatureSlice(id="models", title="models", owner_artifacts=["pkg/domain/models.py"]),
                    FeatureSlice(id="tasks", title="tasks", owner_artifacts=["pkg/domain/tasks.py"]),
                ],
                work_items=[],
                required_artifacts=["pkg/__init__.py", "pkg/domain/models.py", "pkg/domain/tasks.py", "tests/test_ok.py"],
                test_artifacts=["tests/test_ok.py"],
            )

            result = IntegrationJudge(tmpdir).check(contract)

            self.assertFalse(result.ok)
            self.assertIn("canonical_type_redefined", [diag["code"] for diag in result.diagnostics])

    def test_final_gate_runs_declared_public_behavior_flow(self):
        from ContractCoding.quality.gates import IntegrationJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkg"), exist_ok=True)
            with open(os.path.join(tmpdir, "pkg", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("def run():\n    return 'broken'\n")
            contract = ContractSpec(
                goal="public flow",
                product_kernel=ProductKernel(
                    flows=[
                        {
                            "id": "package_run_flow",
                            "kind": "python_behavior_probe",
                            "code": "import pkg\nassert pkg.run() == 'ok'\n",
                        }
                    ],
                    acceptance_matrix=[{"id": "public_behavior_flow"}],
                ),
                feature_slices=[FeatureSlice(id="package_surface", title="package", owner_artifacts=["pkg/__init__.py"])],
                work_items=[],
                required_artifacts=["pkg/__init__.py"],
            )

            result = IntegrationJudge(tmpdir).check(contract)

            self.assertFalse(result.ok)
            self.assertIn("public_flow_failed", [diag["code"] for diag in result.diagnostics])

    def test_semantic_linter_blocks_ungrounded_empty_exports_assertion(self):
        from ContractCoding.quality.gates import IntegrationJudge

        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "Build package named atlas_ops with atlas_ops/__init__.py atlas_ops/core.py tests/test_contract.py"
            )
            for artifact in contract.required_artifacts:
                path = os.path.join(tmpdir, artifact)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if artifact.startswith("tests/"):
                    content = "import atlas_ops\n\ndef test_exports():\n    assert atlas_ops.__all__ == []\n"
                elif artifact.endswith("__init__.py"):
                    content = "__version__ = '0.1.0'\n__all__ = ['__version__']\n"
                else:
                    content = "def run():\n    return 'ok'\n"
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(content)

            result = IntegrationJudge(tmpdir).check(contract)

            self.assertFalse(result.ok)
            self.assertIn("ungrounded_acceptance_assertion", [diag["code"] for diag in result.diagnostics])


if __name__ == "__main__":
    unittest.main()
