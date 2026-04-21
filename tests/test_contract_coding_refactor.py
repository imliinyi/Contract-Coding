from contextlib import contextmanager
import os
import shutil
import tempfile
import unittest
from unittest import mock

from ContractCoding.agents.forge import AgentCapability, AgentForge
from ContractCoding.config import Config
from ContractCoding.memory.document import DocumentManager
from ContractCoding.memory.processor import MemoryProcessor
from ContractCoding.orchestration.harness import TaskHarness
from ContractCoding.orchestration.traverser import GraphTraverser
from ContractCoding.tools.artifacts import ArtifactMetadataStore
from ContractCoding.tools.file_tool import WorkspaceFS
from ContractCoding.utils.state import GeneralState
import main as contract_main


@contextmanager
def local_tempdir():
    root = os.path.join(os.getcwd(), ".tmp_test_runs")
    os.makedirs(root, exist_ok=True)
    path = tempfile.mkdtemp(dir=root)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def build_contract_markdown(files, dependency_lines="") -> str:
    directory_lines = ["workspace/"]
    for file_spec in files:
        directory_lines.append(f"  {file_spec['file']}")

    file_blocks = []
    for file_spec in files:
        depends_on = file_spec.get("depends_on", [])
        depends_line = ""
        if depends_on:
            depends_line = f"\n*   **Depends On:** {', '.join(depends_on)}"

        execution_mode = file_spec.get("execution", "single")
        file_blocks.append(
            "\n".join(
                [
                    f"**File:** `{file_spec['file']}`",
                    f"*   **Module:** {file_spec.get('module', 'Core')}",
                    f"*   **Execution:** {execution_mode}",
                    f"*   **Function:** `{file_spec.get('symbol', 'run')}`",
                    f"*   **Owner:** {file_spec.get('owner', 'Backend_Engineer')}",
                    f"*   **Version:** {file_spec.get('version', 1)}",
                    f"*   **Status:** {file_spec.get('status', 'TODO')}",
                    depends_line.lstrip("\n"),
                ]
            ).strip()
        )

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
{chr(10).join(directory_lines)}
```

### 2.2 Global Shared Knowledge

### 2.3 Dependency Relationships(MUST):
{dependency_lines}

### 2.4 Symbolic API Specifications
{chr(10).join(chr(10) + block for block in file_blocks).strip()}

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
        target = os.path.join(self.workspace_dir, "app.py")
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


class ContractCodingRefactorTests(unittest.TestCase):
    def test_workspace_write_file_uses_sidecar_metadata(self):
        with local_tempdir() as tmpdir:
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

    def test_main_entrypoint_no_longer_raises_name_error(self):
        with local_tempdir() as tmpdir:
            with mock.patch(
                "sys.argv",
                ["main.py", "--workspace", tmpdir, "--log-path", os.path.join(tmpdir, "agent.log")],
            ):
                contract_main.main()

    def test_document_manager_rejects_cross_agent_same_target_conflict(self):
        manager = DocumentManager()
        manager.execute_actions(
            [
                {
                    "type": "add",
                    "agent_name": "Project_Manager",
                    "content": build_contract_markdown(
                        [
                            {
                                "file": "app.py",
                                "module": "runtime",
                                "owner": "Backend_Engineer",
                                "status": "TODO",
                            }
                        ]
                    ),
                }
            ]
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
                            "*   **Module:** runtime\n"
                            "*   **Execution:** single\n"
                            "*   **Function:** `run`\n"
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
                            "*   **Module:** runtime\n"
                            "*   **Execution:** single\n"
                            "*   **Function:** `run`\n"
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

    def test_document_manager_groups_algorithm_contract_into_module_cells(self):
        manager = DocumentManager()
        manager.execute_actions(
            [
                {
                    "type": "add",
                    "agent_name": "Project_Manager",
                    "content": build_contract_markdown(
                        [
                            {
                                "file": "search.py",
                                "module": "Search Core",
                                "owner": "Algorithm_Engineer",
                                "execution": "parallel",
                            },
                            {
                                "file": "heuristics.py",
                                "module": "Search Core",
                                "owner": "Algorithm_Engineer",
                                "execution": "parallel",
                            },
                            {
                                "file": "cli.py",
                                "module": "CLI",
                                "owner": "Backend_Engineer",
                                "depends_on": ["Search Core"],
                            },
                        ]
                    ),
                }
            ]
        )

        modules = manager.get_modules()
        self.assertEqual([module["module"] for module in modules], ["Search Core", "CLI"])
        self.assertEqual(modules[0]["files"], ["search.py", "heuristics.py"])
        self.assertEqual(modules[1]["dependencies"], ["Search Core"])

    def test_graph_traverser_returns_latest_output_state(self):
        with local_tempdir() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"), MAX_LAYERS=4)
            manager = DocumentManager()
            manager.execute_actions(
                [
                    {
                        "type": "add",
                        "agent_name": "Project_Manager",
                        "content": build_contract_markdown(
                            [
                                {
                                    "file": "app.py",
                                    "module": "runtime",
                                    "owner": "Backend_Engineer",
                                    "status": "VERIFIED",
                                }
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
            initial_state = GeneralState(task="demo", sub_task="", role="user", thinking="", output="")

            _, _, terminating_states = traverser.traverse("Project_Manager", initial_state)

            self.assertEqual(len(terminating_states), 1)
            self.assertEqual(terminating_states[0].output, "Architect-output")
            self.assertEqual(terminating_states[0].role, "Architect")

    def test_graph_traverser_schedules_parallel_module_packets_by_owner(self):
        manager = DocumentManager()
        manager.execute_actions(
            [
                {
                    "type": "add",
                    "agent_name": "Project_Manager",
                    "content": build_contract_markdown(
                        [
                            {
                                "file": "frontend/ui.tsx",
                                "module": "Experience",
                                "owner": "Frontend_Engineer",
                                "execution": "parallel",
                            },
                            {
                                "file": "backend/runtime.py",
                                "module": "Experience",
                                "owner": "Backend_Engineer",
                                "execution": "parallel",
                            },
                        ]
                    ),
                }
            ]
        )

        traverser = GraphTraverser(
            config=Config(WORKSPACE_DIR="workspace", LOG_PATH=os.devnull),
            agent_runner=FakeRunner(),
            memory_processor=MemoryProcessor(Config(), ["Project_Manager", "Architect"], 100),
            document_manager=manager,
        )

        schedule = traverser._schedule_from_contract(
            manager.get(),
            {"Architect": [GeneralState(task="demo", sub_task="", role="user", thinking="", output="")]},
        )

        self.assertIn("Frontend_Engineer", schedule)
        self.assertIn("Backend_Engineer", schedule)
        self.assertIn("Module Cell: Experience", schedule["Frontend_Engineer"][0].sub_task)
        self.assertIn("Module Cell: Experience", schedule["Backend_Engineer"][0].sub_task)

    def test_graph_traverser_respects_serial_execution_with_module_dependencies(self):
        manager = DocumentManager()
        manager.execute_actions(
            [
                {
                    "type": "add",
                    "agent_name": "Project_Manager",
                    "content": build_contract_markdown(
                        [
                            {
                                "file": "search.py",
                                "module": "Search Core",
                                "owner": "Algorithm_Engineer",
                                "execution": "single",
                                "status": "TODO",
                            },
                            {
                                "file": "heuristics.py",
                                "module": "Search Core",
                                "owner": "Algorithm_Engineer",
                                "execution": "single",
                                "status": "TODO",
                                "depends_on": ["search.py"],
                            },
                            {
                                "file": "runner.py",
                                "module": "Runner",
                                "owner": "Backend_Engineer",
                                "status": "TODO",
                                "depends_on": ["Search Core"],
                            },
                        ]
                    ),
                }
            ]
        )

        traverser = GraphTraverser(
            config=Config(WORKSPACE_DIR="workspace", LOG_PATH=os.devnull),
            agent_runner=FakeRunner(),
            memory_processor=MemoryProcessor(Config(), ["Project_Manager", "Architect"], 100),
            document_manager=manager,
        )

        schedule = traverser._schedule_from_contract(
            manager.get(),
            {"Architect": [GeneralState(task="demo", sub_task="", role="user", thinking="", output="")]},
        )

        self.assertIn("Algorithm_Engineer", schedule)
        self.assertNotIn("Backend_Engineer", schedule)
        self.assertIn("Primary target file: `search.py`", schedule["Algorithm_Engineer"][0].sub_task)
        self.assertNotIn("`heuristics.py`", schedule["Algorithm_Engineer"][0].sub_task)

    def test_agent_forge_wires_search_tool(self):
        with local_tempdir() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            forge = AgentForge(config)
            agent = forge.create_agent("Researcher", AgentCapability(FILE=True, SEARCH=True))
            tool_names = {tool.__name__ for tool in agent.custom_tools}
            self.assertIn("search_web", tool_names)

    def test_task_harness_records_invalid_implementation(self):
        with local_tempdir() as tmpdir:
            config = Config(WORKSPACE_DIR=tmpdir, LOG_PATH=os.path.join(tmpdir, "agent.log"))
            manager = DocumentManager()
            manager.execute_actions(
                [
                    {
                        "type": "add",
                        "agent_name": "Project_Manager",
                        "content": build_contract_markdown(
                            [
                                {
                                    "file": "app.py",
                                    "module": "runtime",
                                    "owner": "Backend_Engineer",
                                    "status": "TODO",
                                }
                            ]
                        ),
                    }
                ]
            )

            harness = TaskHarness(config, manager)
            result = harness.execute(
                agent=DummyImplementationAgent(tmpdir),
                agent_name="Backend_Engineer",
                state=GeneralState(
                    task="demo",
                    sub_task=(
                        "Module Cell: runtime\n"
                        "Primary target file: `app.py`\n"
                        "Assigned files in this module packet:\n"
                        "- `app.py`\n"
                    ),
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


if __name__ == "__main__":
    unittest.main()
