import tempfile
import unittest

from ContractCoding.llm.openai_backend import OpenAIBackend
from ContractCoding.tools.file_tool import WorkspaceFS, build_file_tools
from ContractCoding.tools.governor import ToolGovernor


class _TimeoutCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise TimeoutError("request timed out")


class _TimeoutClient:
    def __init__(self):
        self.completions = _TimeoutCompletions()
        self.chat = self


class ToolRuntimeProtocolTests(unittest.TestCase):
    def test_replace_file_truncates_old_scaffold_tail(self):
        with tempfile.TemporaryDirectory() as workspace:
            fs = WorkspaceFS(workspace)
            fs.write_file(
                "pkg/save_load.py",
                "def save_colony(*args):\n"
                "    raise NotImplementedError('scaffold pending implementation')\n"
                "\n"
                "def leftover():\n"
                "    pass\n",
            )

            result = fs.replace_file("pkg/save_load.py", "VALUE = 1\n")

            self.assertIn("File successfully written", result)
            content = fs.read_file("pkg/save_load.py")
            self.assertEqual(content, "VALUE = 1\n")
            self.assertNotIn("NotImplementedError", content)
            self.assertNotIn("pass", content)

    def test_replace_file_is_available_and_scoped_as_write_tool(self):
        tool_names = {tool.__name__ for tool in build_file_tools(".")}
        self.assertIn("replace_file", tool_names)

        governor = ToolGovernor(
            approval_mode="auto-edit",
            allowed_tools=["replace_file"],
            allowed_artifacts=["pkg/save_load.py"],
        )

        allowed = governor.decide("replace_file", {"path": "pkg/save_load.py"})
        denied = governor.decide("replace_file", {"path": "pkg/other.py"})

        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)

    def test_openai_tool_loop_returns_after_bounded_timeout_retries(self):
        with tempfile.TemporaryDirectory() as workspace:
            backend = object.__new__(OpenAIBackend)
            backend.client = _TimeoutClient()
            backend.model = "test-model"
            backend.max_tokens = 128
            backend.temperature = 0
            backend.tool_approval_mode = "auto-edit"
            backend.tool_timeout = 1
            backend.tool_loop_timeout = 5
            backend.max_tool_iterations = 10
            backend.workspace_dir = workspace
            backend.allowed_artifacts = []
            backend.allowed_conflict_keys = []
            backend.allowed_tools = []
            backend.repair_diagnostics_text = ""
            backend._last_attempts = []

            response = backend.chat_with_tools([{"role": "user", "content": "implement"}], [])

            self.assertEqual(response.raw["failure_kind"], "timeout")
            self.assertEqual(response.raw["stop_reason"], "timeout")
            self.assertEqual(response.raw["tool_iterations"], 1)
            self.assertEqual(len(response.raw["attempts"]), 2)
            self.assertEqual(backend.client.completions.calls, 2)


if __name__ == "__main__":
    unittest.main()
