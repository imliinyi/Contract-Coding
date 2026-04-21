import os
import subprocess
import tempfile
import unittest
from unittest import mock

from ContractCoding.agents.forge import AgentCapability, AgentForge
from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.execution_plane import ExecutionPlaneManager, ExecutionPlanePromotionError
from ContractCoding.orchestration.harness import TaskHarness
from ContractCoding.orchestration.workspace_context import get_current_workspace, workspace_scope
from ContractCoding.orchestration.traverser import GraphTraverser
from ContractCoding.tools.artifacts import ArtifactMetadataStore
from ContractCoding.tools.file_tool import build_file_tools
from ContractCoding.tools.file_tool import WorkspaceFS
from ContractCoding.utils.state import GeneralState
import main as contract_main


def build_contract_markdown(status: str = "TODO", tasks: list[dict] | None = None) -> str:
    task_specs = tasks or [
        {
            "file": "app.py",
            "module": "app",
            "owner": "Backend_Engineer",
            "version": 1,
            "status": status,
            "depends_on": [],
            "class_name": "App",
        }
    ]
    directory_lines = "\n".join(f"workspace/{task['file']}" for task in task_specs)
    task_blocks = []
    for task in task_specs:
        dependencies = task.get("depends_on", [])
        depends_on = ", ".join(f"`{item}`" for item in dependencies) if dependencies else "None"
        task_blocks.append(
            "\n".join(
                [
                    f"**File:** `{task['file']}`",
                    f"*   **Module:** {task.get('module', 'root')}",
                    f"*   **Depends On:** {depends_on}",
                    f"*   **Class:** `{task.get('class_name', 'App')}`",
                    f"*   **Owner:** {task.get('owner', 'Backend_Engineer')}",
                    f"*   **Version:** {task.get('version', 1)}",
                    f"*   **Status:** {task.get('status', status)}",
                ]
            )
        )
    task_blocks_body = "\n\n".join(task_blocks)

    return f"""## Product Requirement Document (PRD)

### 1.1 Project Overview
Minimal contract

### 1.2 User Stories (Features)
*   **Feature:** Minimal

### 1.3 Constraints
*   **Tech Stack:** Python

## Technical Architecture Document (System Design)

### 2.1 Directory Structure
```text
{directory_lines}
```

### 2.2 Global Shared Knowledge

### 2.3 Dependency Relationships(MUST):

### 2.4 Symbolic API Specifications
{task_blocks_body}

### Status Model & Termination Guard
- Status in one line: use `TODO/DONE/ERROR/VERIFIED`; end only when all are `VERIFIED`.
"""


class FakeRunner:
    def __init__(self):
        self.agents = {"Project_Manager": object(), "Architect": object()}

    def run(self, agent_name: str, state: GeneralState, next_available_agents: list) -> GeneralState:
        return GeneralState(
            task=state.task,
            sub_task=state.sub_task,
            role=agent_name,
            thinking=f"{agent_name}-thinking",
            output=f"{agent_name}-output",
            next_agents=[],
            task_requirements={},
        )


class DummyImplementationAgent:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def _execute_agent(self, state, next_available_agents, document_manager, memory_processor):
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

    def _execute_agent(self, state, next_available_agents, document_manager, memory_processor):
        runtime_workspace = get_current_workspace(self.workspace_dir)
        target = os.path.join(runtime_workspace, "app.py")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("def run():\n    return 'ok'\n")

        document_manager.execute_actions(
            [
                {
                    "type": "update",
                    "agent_name": "Backend_Engineer",
                    "content": {
                        "Symbolic API Specifications": (
                            "**File:** `app.py`\n"
                            "*   **Class:** `App`\n"
                            "*   **Owner:** Backend_Engineer\n"
                            "*   **Version:** 2\n"
                            "*   **Status:** DONE"
                        )
                    },
                }
            ]
        )

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

    def test_document_manager_rejects_cross_agent_same_target_conflict(self):
        manager = DocumentManager()
        manager.execute_actions(
            [{"type": "add", "agent_name": "Project_Manager", "content": build_contract_markdown(status="TODO")}]
        )

        manager.begin_layer_aggregation(manager.get_version())
        manager.queue_actions(
            [
                {
                    "type": "update",
                    "agent_name": "Backend_Engineer",
                    "content": {
                        "Symbolic API Specifications": (
                            "**File:** `app.py`\n"
                            "*   **Class:** `App`\n"
                            "*   **Owner:** Backend_Engineer\n"
                            "*   **Version:** 1\n"
                            "*   **Status:** DONE"
                        )
                    },
                },
                {
                    "type": "update",
                    "agent_name": "Critic",
                    "content": {
                        "Symbolic API Specifications": (
                            "**File:** `app.py`\n"
                            "*   **Class:** `App`\n"
                            "*   **Owner:** Backend_Engineer\n"
                            "*   **Version:** 1\n"
                            "*   **Status:** ERROR\n"
                            "- wrong implementation"
                        )
                    },
                },
            ]
        )
        manager.commit_layer_aggregation()

        task = manager.get_task("app.py")
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "DONE")
        self.assertEqual(len(manager.get_last_conflicts()), 1)

    def test_document_manager_persists_under_workspace_contract_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = DocumentManager(workspace_dir=tmpdir)
            manager.execute_actions(
                [{"type": "add", "agent_name": "Project_Manager", "content": build_contract_markdown(status="TODO")}]
            )

            self.assertTrue(manager.document_path.startswith(os.path.join(tmpdir, ".contractcoding")))
            self.assertTrue(os.path.exists(manager.document_path))

    def test_partial_task_updates_preserve_module_metadata(self):
        manager = DocumentManager()
        manager.execute_actions(
            [
                {
                    "type": "add",
                    "agent_name": "Project_Manager",
                    "content": build_contract_markdown(
                        tasks=[
                            {
                                "file": "ui/screen.tsx",
                                "module": "ui/chat",
                                "owner": "Frontend_Engineer",
                                "status": "TODO",
                                "depends_on": ["core/api.py"],
                                "class_name": "Screen",
                            },
                            {
                                "file": "core/api.py",
                                "module": "core/runtime",
                                "owner": "Backend_Engineer",
                                "status": "VERIFIED",
                                "depends_on": [],
                                "class_name": "ApiService",
                            },
                        ]
                    ),
                }
            ]
        )

        manager.execute_actions(
            [
                {
                    "type": "update",
                    "agent_name": "Frontend_Engineer",
                    "content": {
                        "Symbolic API Specifications": (
                            "**File:** `ui/screen.tsx`\n"
                            "*   **Class:** `Screen`\n"
                            "*   **Owner:** Frontend_Engineer\n"
                            "*   **Version:** 2\n"
                            "*   **Status:** DONE"
                        )
                    },
                }
            ]
        )

        task = manager.get_task("ui/screen.tsx")
        self.assertIsNotNone(task)
        self.assertEqual(task.module, "ui/chat")
        self.assertEqual(task.depends_on, ["core/api.py"])
        self.assertEqual(task.status, "DONE")

    def test_graph_traverser_returns_latest_output_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), MAX_LAYERS=4)
            manager = DocumentManager()
            manager.execute_actions(
                [{"type": "add", "agent_name": "Project_Manager", "content": build_contract_markdown(status="VERIFIED")}]
            )

            traverser = GraphTraverser(
                config=config,
                agent_runner=FakeRunner(),
                memory_processor=MemoryProcessor(config, ["Project_Manager", "Architect"], 100),
                document_manager=manager,
            )
            initial_state = GeneralState(task="demo", sub_task="", role="user", thinking="", output="")

            _, _, terminating_states = traverser.traverse("Project_Manager", initial_state)

            self.assertEqual(len(terminating_states), 1)
            self.assertEqual(terminating_states[0].output, "Architect-output")
            self.assertEqual(terminating_states[0].role, "Architect")

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
            manager = DocumentManager()
            manager.execute_actions(
                [{"type": "add", "agent_name": "Project_Manager", "content": build_contract_markdown(status="TODO")}]
            )

            harness = TaskHarness(config, manager)
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
                memory_processor=None,
            )

            self.assertTrue(result.validation_errors)
            task = manager.get_task("app.py")
            self.assertIsNotNone(task)
            self.assertEqual(task.status, "ERROR")
            self.assertIn("pass", task.render())

    def test_task_harness_sandbox_promotes_successful_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(
                WORKSPACE_DIR=tmpdir,
                LOG_PATH=os.path.join(tmpdir, "agent.log"),
                EXECUTION_PLANE="sandbox",
            )
            manager = DocumentManager()
            manager.execute_actions(
                [{"type": "add", "agent_name": "Project_Manager", "content": build_contract_markdown(status="TODO")}]
            )

            harness = TaskHarness(config, manager)
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
                memory_processor=None,
            )

            self.assertEqual(result.validation_errors, [])
            with open(os.path.join(tmpdir, "app.py"), "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("return 'ok'", content)
            task = manager.get_task("app.py")
            self.assertIsNotNone(task)
            self.assertEqual(task.status, "DONE")

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

    def test_worktree_plane_inherits_dirty_workspace_snapshot(self):
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as runtime_dir:
            subprocess.run(["git", "-C", repo_dir, "init"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-C", repo_dir, "config", "user.email", "codex@example.com"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", repo_dir, "config", "user.name", "Codex"],
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

    def test_document_manager_compiles_module_team_plans(self):
        manager = DocumentManager()
        manager.execute_actions(
            [
                {
                    "type": "add",
                    "agent_name": "Project_Manager",
                    "content": build_contract_markdown(
                        tasks=[
                            {
                                "file": "core/api.py",
                                "module": "core/runtime",
                                "owner": "Backend_Engineer",
                                "status": "TODO",
                                "depends_on": [],
                                "class_name": "ApiService",
                            },
                            {
                                "file": "ui/widget.tsx",
                                "module": "ui/chat",
                                "owner": "Frontend_Engineer",
                                "status": "DONE",
                                "depends_on": [],
                                "class_name": "Widget",
                            },
                            {
                                "file": "ui/screen.tsx",
                                "module": "ui/chat",
                                "owner": "Frontend_Engineer",
                                "status": "TODO",
                                "depends_on": ["core/api.py"],
                                "class_name": "Screen",
                            },
                        ]
                    ),
                }
            ]
        )

        plans = {plan.name: plan for plan in manager.get_module_plans()}
        self.assertEqual(plans["core/runtime"].ready_tasks[0]["file"], "core/api.py")
        self.assertEqual(plans["ui/chat"].blocked_tasks[0]["blocked_by"], ["core/api.py"])
        self.assertEqual(plans["ui/chat"].review_tasks[0]["file"], "ui/widget.tsx")
        self.assertEqual(plans["ui/chat"].module_dependencies, ["core/runtime"])

    def test_graph_traverser_schedules_module_teams_and_review_barriers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), MAX_LAYERS=4)
            manager = DocumentManager()
            manager.execute_actions(
                [
                    {
                        "type": "add",
                        "agent_name": "Project_Manager",
                        "content": build_contract_markdown(
                            tasks=[
                                {
                                    "file": "core/api.py",
                                    "module": "core/runtime",
                                    "owner": "Backend_Engineer",
                                    "status": "TODO",
                                    "depends_on": [],
                                    "class_name": "ApiService",
                                },
                                {
                                    "file": "ui/widget.tsx",
                                    "module": "ui/chat",
                                    "owner": "Frontend_Engineer",
                                    "status": "DONE",
                                    "depends_on": [],
                                    "class_name": "Widget",
                                },
                                {
                                    "file": "ui/screen.tsx",
                                    "module": "ui/chat",
                                    "owner": "Frontend_Engineer",
                                    "status": "TODO",
                                    "depends_on": ["core/api.py"],
                                    "class_name": "Screen",
                                },
                            ]
                        ),
                    }
                ]
            )

            traverser = GraphTraverser(
                config=config,
                agent_runner=FakeRunner(),
                memory_processor=MemoryProcessor(config, ["Project_Manager", "Architect"], 100),
                document_manager=manager,
            )
            base_state = GeneralState(task="demo", sub_task="", role="user", thinking="", output="")
            next_map = traverser._schedule_from_contract(manager.get(), {"Architect": [base_state]})

            self.assertIn("Backend_Engineer", next_map)
            self.assertNotIn("Frontend_Engineer", next_map)
            self.assertIn("Critic", next_map)
            self.assertIn("Code_Reviewer", next_map)
            self.assertIn("Module team: core/runtime", next_map["Backend_Engineer"][0].sub_task)
            self.assertIn("Target files in this module wave:", next_map["Backend_Engineer"][0].sub_task)
            self.assertIn("Module team: ui/chat", next_map["Critic"][0].sub_task)
            self.assertIn("Files to review:", next_map["Critic"][0].sub_task)

    def test_task_harness_parses_module_wave_packets_with_multiple_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = DocumentManager()
            manager.execute_actions(
                [
                    {
                        "type": "add",
                        "agent_name": "Project_Manager",
                        "content": build_contract_markdown(
                            tasks=[
                                {
                                    "file": "core/api.py",
                                    "module": "core/runtime",
                                    "owner": "Backend_Engineer",
                                    "status": "TODO",
                                    "depends_on": [],
                                    "class_name": "ApiService",
                                },
                                {
                                    "file": "core/store.py",
                                    "module": "core/runtime",
                                    "owner": "Backend_Engineer",
                                    "status": "TODO",
                                    "depends_on": [],
                                    "class_name": "Store",
                                },
                            ]
                        ),
                    }
                ]
            )

            harness = TaskHarness(config, manager)
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


if __name__ == "__main__":
    unittest.main()
