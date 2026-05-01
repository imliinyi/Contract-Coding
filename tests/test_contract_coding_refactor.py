import os
import subprocess
import tempfile
import unittest
from unittest import mock

from ContractCoding.agents.forge import AgentCapability, AgentForge
from ContractCoding.config import Config
from ContractCoding.contract.compiler import ContractCompiler
from ContractCoding.runtime.store import RunStore
from ContractCoding.runtime.scheduler import Scheduler
from ContractCoding.execution.planes import ExecutionPlaneManager, ExecutionPlanePromotionError
from ContractCoding.execution.harness import TaskHarness
from ContractCoding.execution.workspace import get_current_workspace, workspace_scope
from ContractCoding.tools.artifacts import ArtifactMetadataStore
from ContractCoding.tools.file_tool import build_file_tools
from ContractCoding.tools.file_tool import WorkspaceFS
from ContractCoding.utils.state import GeneralState
import main as contract_main


class DummyImplementationAgent:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def _execute_agent(self, state, next_available_agents, context_manager):
        target = os.path.join(get_current_workspace(self.workspace_dir), "app.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("def run():\n    pass\n")
        return GeneralState(
            task=state.task,
            sub_task=state.sub_task,
            role="Backend_Engineer",
            thinking="wrote file",
            output="done",
            next_agents=[],
            task_requirements={},
        )


class SuccessfulImplementationAgent:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def _execute_agent(self, state, next_available_agents, context_manager):
        runtime_workspace = get_current_workspace(self.workspace_dir)
        target = os.path.join(runtime_workspace, "app.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("def run():\n    return 'ok'\n")

        return GeneralState(
            task=state.task,
            sub_task=state.sub_task,
            role="Backend_Engineer",
            thinking="wrote valid file",
            output="done",
            next_agents=[],
            task_requirements={},
        )


class ContractCodingRefactorTests(unittest.TestCase):
    def test_file_tools_follow_runtime_workspace_scope(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as isolated_dir:
            tools = {tool.__name__: tool for tool in build_file_tools(base_dir)}
            with workspace_scope(isolated_dir):
                result = tools["write_file"]("demo.txt", "hello")

            self.assertIn("artifact version 1", result)
            self.assertFalse(os.path.exists(os.path.join(base_dir, "demo.txt")))
            self.assertTrue(os.path.exists(os.path.join(isolated_dir, "demo.txt")))

    def test_workspace_write_file_uses_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs = WorkspaceFS(tmpdir)
            store = ArtifactMetadataStore(tmpdir)

            write_result = fs.write_file("src/app.ts", "console.log('hello');\n")
            self.assertIn("artifact version 1", write_result)

            with open(os.path.join(tmpdir, "src", "app.ts"), "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertEqual(content, "console.log('hello');\n")
            self.assertEqual(store.get_version(os.path.join(tmpdir, "src", "app.ts")), 1)

            second_write = fs.write_file("src/app.ts", "console.log('updated');\n")
            self.assertIn("artifact version 2", second_write)
            self.assertEqual(store.get_version(os.path.join(tmpdir, "src", "app.ts")), 2)
            self.assertTrue(
                os.path.exists(os.path.join(tmpdir, ".contractcoding", "artifacts", "src", "app.ts.json"))
            )
            self.assertFalse(os.path.exists(os.path.join(tmpdir, ".contractcoding", "artifacts.json")))

    def test_workspace_write_file_preserves_python_escape_sequences(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs = WorkspaceFS(tmpdir)

            fs.write_file("engine.py", 'def render(rows):\n    return "\\n".join(rows)\n')

            with open(os.path.join(tmpdir, "engine.py"), "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertEqual(content, 'def render(rows):\n    return "\\n".join(rows)\n')

    def test_workspace_write_file_allows_markdown_contract_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs = WorkspaceFS(tmpdir)
            store = ArtifactMetadataStore(tmpdir)

            result = fs.write_file(".contractcoding/output.md", "# Memo\n\nDone.\n")

            self.assertIn("artifact version", result)
            with open(os.path.join(tmpdir, ".contractcoding", "output.md"), "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "# Memo\n\nDone.\n")
            self.assertEqual(store.get_version(os.path.join(tmpdir, ".contractcoding", "output.md")), 1)
            self.assertTrue(
                os.path.exists(
                    os.path.join(
                        tmpdir,
                        ".contractcoding",
                        "artifacts",
                        ".contractcoding",
                        "output.md.json",
                    )
                )
            )

    def test_artifact_metadata_is_stored_per_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs = WorkspaceFS(tmpdir)
            fs.write_file("src/app.ts", "console.log('a');\n")
            fs.write_file("src/other.ts", "console.log('b');\n")

            self.assertTrue(
                os.path.exists(os.path.join(tmpdir, ".contractcoding", "artifacts", "src", "app.ts.json"))
            )
            self.assertTrue(
                os.path.exists(os.path.join(tmpdir, ".contractcoding", "artifacts", "src", "other.ts.json"))
            )

    def test_main_entrypoint_no_longer_raises_name_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "sys.argv",
                ["main.py", "--workspace", tmpdir, "--log-path", os.path.join(tmpdir, "agent.log")],
            ):
                contract_main.main()

    def test_agent_forge_wires_search_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            forge = AgentForge(config)
            agent = forge.create_agent("Researcher", AgentCapability(FILE=True, SEARCH=True))
            tool_names = {tool.__name__ for tool in agent.custom_tools}
            self.assertIn("search_web", tool_names)

    def test_task_harness_records_invalid_implementation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))

            harness = TaskHarness(config)
            result = harness.execute(
                agent=DummyImplementationAgent(tmpdir),
                agent_name="Backend_Engineer",
                state=GeneralState(
                    task="demo",
                    sub_task="Implement/Fix app.py. Current Status: TODO.",
                    role="user",
                    thinking="",
                    output="",
                ),
                next_available_agents=[],
                context_manager=None,
            )

            self.assertTrue(result.validation_errors)
            self.assertTrue(any("Placeholder" in error for error in result.validation_errors))

    def test_task_harness_sandbox_promotes_successful_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                EXECUTION_PLANE="sandbox",
            )

            harness = TaskHarness(config)
            result = harness.execute(
                agent=SuccessfulImplementationAgent(tmpdir),
                agent_name="Backend_Engineer",
                state=GeneralState(
                    task="demo",
                    sub_task="Implement/Fix app.py. Current Status: TODO.",
                    role="user",
                    thinking="",
                    output="",
                ),
                next_available_agents=[],
                context_manager=None,
            )

            self.assertEqual(result.validation_errors, [])
            with open(os.path.join(tmpdir, "app.py"), "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("return 'ok'", content)

    def test_execution_plane_promotion_rejects_newer_workspace_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "app.py"), "w", encoding="utf-8") as handle:
                handle.write("print('base')\n")

            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                EXECUTION_PLANE="sandbox",
            )
            manager = ExecutionPlaneManager(config)
            plane = manager.acquire("core/runtime", isolated=True)
            try:
                with open(os.path.join(plane.working_dir, "app.py"), "w", encoding="utf-8") as handle:
                    handle.write("print('plane')\n")
                with open(os.path.join(tmpdir, "app.py"), "w", encoding="utf-8") as handle:
                    handle.write("print('newer')\n")

                with self.assertRaises(ExecutionPlanePromotionError):
                    manager.promote(plane, {"app.py"})

                with open(os.path.join(tmpdir, "app.py"), "r", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "print('newer')\n")
            finally:
                manager.cleanup(plane)

    def test_execution_plane_promotion_auto_merges_non_overlapping_text_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "app.py"), "w", encoding="utf-8") as handle:
                handle.write("line1\nline2\nline3\n")

            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                EXECUTION_PLANE="sandbox",
            )
            manager = ExecutionPlaneManager(config)
            plane = manager.acquire("core/runtime", isolated=True)
            try:
                with open(os.path.join(plane.working_dir, "app.py"), "w", encoding="utf-8") as handle:
                    handle.write("line1-agent\nline2\nline3\n")
                with open(os.path.join(tmpdir, "app.py"), "w", encoding="utf-8") as handle:
                    handle.write("line1\nline2\nline3-user\n")

                promoted = manager.promote(plane, {"app.py"})
                self.assertEqual(promoted, {"app.py"})
                with open(os.path.join(tmpdir, "app.py"), "r", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "line1-agent\nline2\nline3-user\n")
            finally:
                manager.cleanup(plane)

    def test_execution_plane_snapshots_ignore_runtime_sqlite_ledger_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".contractcoding"), exist_ok=True)
            with open(os.path.join(tmpdir, ".contractcoding", "runs.sqlite-journal"), "w", encoding="utf-8") as handle:
                handle.write("transient")
            with open(os.path.join(tmpdir, "app.py"), "w", encoding="utf-8") as handle:
                handle.write("VALUE = 1\n")

            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                EXECUTION_PLANE="sandbox",
            )
            manager = ExecutionPlaneManager(config)
            plane = manager.acquire("core/runtime", isolated=True)
            try:
                self.assertFalse(os.path.exists(os.path.join(plane.working_dir, ".contractcoding", "runs.sqlite-journal")))
                self.assertFalse(os.path.exists(os.path.join(plane.baseline_dir, ".contractcoding", "runs.sqlite-journal")))
                self.assertTrue(os.path.exists(os.path.join(plane.working_dir, "app.py")))
            finally:
                manager.cleanup(plane)

    def test_worktree_plane_inherits_dirty_workspace_snapshot(self):
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as runtime_dir:
            subprocess.run(["git", "-C", repo_dir, "init"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", repo_dir, "config", "user.email", "agent@example.com"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", repo_dir, "config", "user.name", "ContractCoding"],
                check=True,
                capture_output=True,
                text=True,
            )
            with open(os.path.join(repo_dir, "app.py"), "w", encoding="utf-8") as handle:
                handle.write("print('committed')\n")
            subprocess.run(["git", "-C", repo_dir, "add", "app.py"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", repo_dir, "commit", "-m", "init"],
                check=True,
                capture_output=True,
                text=True,
            )

            with open(os.path.join(repo_dir, "app.py"), "w", encoding="utf-8") as handle:
                handle.write("print('dirty')\n")

            config = Config(
                WORKSPACE_DIR=repo_dir,
                LOG_PATH=os.path.join(repo_dir, "agent.log"),
                EXECUTION_PLANE="worktree",
                EXECUTION_ROOT=runtime_dir,
                FALLBACK_TO_SANDBOX=False,
            )
            manager = ExecutionPlaneManager(config)
            plane = manager.acquire("core/runtime", isolated=True)
            try:
                self.assertEqual(plane.mode, "worktree")
                with open(os.path.join(plane.working_dir, "app.py"), "r", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "print('dirty')\n")
            finally:
                manager.cleanup(plane)

    def test_scheduler_schedules_implementation_team_waves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = ContractCompiler().compile(
                "demo",
                {
                    "goals": ["demo"],
                    "work_scopes": [
                        {"id": "core/runtime", "type": "code_module", "artifacts": ["core/api.py"]},
                        {"id": "ui/chat", "type": "code_module", "artifacts": ["ui/widget.tsx", "ui/screen.tsx"]},
                    ],
                    "work_items": [
                    {
                        "id": "coding:core/api.py",
                        "kind": "coding",
                        "title": "API service",
                        "scope_id": "core/runtime",
                        "target_artifacts": ["core/api.py"],
                        "owner_profile": "Backend_Engineer",
                        "status": "TODO",
                        "acceptance_criteria": ["core/api.py implements the API service"],
                    },
                    {
                        "id": "coding:ui/widget.tsx",
                        "kind": "coding",
                        "title": "Widget",
                        "scope_id": "ui/chat",
                        "target_artifacts": ["ui/widget.tsx"],
                        "owner_profile": "Frontend_Engineer",
                        "status": "DONE",
                        "acceptance_criteria": ["ui/widget.tsx implements the widget"],
                    },
                    {
                        "id": "coding:ui/screen.tsx",
                        "kind": "coding",
                        "title": "Screen",
                        "scope_id": "ui/chat",
                        "target_artifacts": ["ui/screen.tsx"],
                        "owner_profile": "Frontend_Engineer",
                        "status": "TODO",
                        "depends_on": ["coding:core/api.py"],
                        "acceptance_criteria": ["ui/screen.tsx implements the screen"],
                    },
                    ],
                },
            )
            store = RunStore.for_workspace(tmpdir)
            run_id = store.create_run("demo", tmpdir, contract=contract)
            waves = Scheduler(store).next_wave(run_id)

            implementation_items = {
                item.id
                for wave in waves
                if wave.wave_kind == "implementation"
                for item in wave.items
            }
            self.assertIn("coding:core/api.py", implementation_items)
            self.assertNotIn("coding:ui/screen.tsx", implementation_items)
            self.assertNotIn("coding:ui/widget.tsx", implementation_items)
            self.assertTrue(any(wave.execution_plane == "worktree" for wave in waves))

            store.update_work_item_status(run_id, "coding:core/api.py", "RUNNING")
            store.update_work_item_status(run_id, "coding:core/api.py", "VERIFIED")
            next_waves = Scheduler(store).next_wave(run_id)
            next_implementation_items = {
                item.id
                for wave in next_waves
                if wave.wave_kind == "implementation"
                for item in wave.items
            }
            self.assertIn("coding:ui/screen.tsx", next_implementation_items)

    def test_task_harness_parses_module_wave_packets_with_multiple_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))

            harness = TaskHarness(config)
            state = GeneralState(
                task="demo",
                sub_task=(
                    "Module team: core/runtime\n"
                    "Owner packet: Backend_Engineer\n"
                    "Implement/Fix the ready files in this module wave.\n"
                    "Target files in this module wave:\n"
                    "- core/api.py\n"
                    "- core/store.py\n"
                ),
                role="user",
                thinking="",
                output="",
            )

            spec = harness.build_spec("Backend_Engineer", state)
            self.assertEqual(spec.target_module, "core/runtime")
            self.assertEqual(spec.target_files, {"core/api.py", "core/store.py"})
            self.assertEqual(spec.owned_files, {"core/api.py", "core/store.py"})

    def test_task_harness_distinguishes_item_targets_from_parallel_wave_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            harness = TaskHarness(config)
            state = GeneralState(
                task="demo",
                sub_task=(
                    "Module team: game\n"
                    "Wave allowed artifacts:\n"
                    "- game_engine.py\n"
                    "- main.py\n\n"
                    "Target files in this module wave:\n"
                    "- game_engine.py\n"
                ),
                role="user",
                thinking="",
                output="",
            )

            spec = harness.build_spec("Backend_Engineer", state)

            self.assertEqual(spec.target_files, {"game_engine.py"})
            self.assertEqual(spec.owned_files, {"game_engine.py", "main.py"})

    def test_task_harness_parses_execution_plane_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            harness = TaskHarness(config)
            state = GeneralState(
                task="demo",
                sub_task=(
                    "Module team: core/runtime\n"
                    "Execution plane: worktree\n"
                    "Target files in this module wave:\n"
                    "- core/api.py\n"
                ),
                role="user",
                thinking="",
                output="",
            )

            spec = harness.build_spec("Backend_Engineer", state)

            self.assertEqual(spec.execution_plane, "worktree")


if __name__ == "__main__":
    unittest.main()
