import os
import shutil
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

langgraph_module = types.ModuleType("langgraph")
langgraph_graph_module = types.ModuleType("langgraph.graph")
langgraph_constants_module = types.ModuleType("langgraph.constants")
langgraph_graph_module.END = "__end__"
langgraph_constants_module.END = "__end__"
sys.modules.setdefault("langgraph", langgraph_module)
sys.modules.setdefault("langgraph.graph", langgraph_graph_module)
sys.modules.setdefault("langgraph.constants", langgraph_constants_module)

from ContractCoding.agents.agent import LLMAgent
from ContractCoding.llm.client import LLM
from ContractCoding.tools.file_tool import WorkspaceFS


class CodexCliBackendTests(unittest.TestCase):
    def test_codex_prompt_enforces_read_only_file_write_contract(self):
        llm = LLM(
            api_key="",
            api_base="",
            deployment_name="codex",
            backend="codex_cli",
            codex_cli_command="codex exec --sandbox read-only --ask-for-approval never -",
        )

        prompt = llm._messages_to_codex_prompt([{"role": "user", "content": "Implement app.py"}])

        self.assertIn("MUST NOT write", prompt)
        self.assertIn("read-only Codex CLI sandbox", prompt)
        self.assertIn("<file_write path=", prompt)

    def test_file_write_extraction_only_accepts_matching_target_path(self):
        response = """
        <file_write path="service.py">
        ```python
        def run() -> bool:
            return True
        ```
        </file_write>
        <file_write path="other.py">
        ```python
        hacked = True
        ```
        </file_write>
        """

        content = LLMAgent._extract_codex_file_content(response, "service.py")
        other = LLMAgent._extract_codex_file_content(response, "main.py")

        self.assertIn("def run()", content)
        self.assertIsNone(other)

    def test_materialize_codex_response_writes_only_scheduler_target(self):
        tmp = os.path.join(os.getcwd(), "codex_cli_test_workspace")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        try:
            agent = object.__new__(LLMAgent)
            agent.logger = Mock()
            fs = WorkspaceFS(tmp)
            response = """
            <file_write path="workspace/service.py">
            ```python
            def answer():
                return 42
            ```
            </file_write>
            <file_write path="outside.py">
            ```python
            raise SystemExit(1)
            ```
            </file_write>
            """

            ok = agent._materialize_codex_file_response(response, "service.py", fs)

            self.assertTrue(ok)
            self.assertTrue(os.path.exists(os.path.join(tmp, "service.py")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "outside.py")))
            with open(os.path.join(tmp, "service.py"), "r", encoding="utf-8") as f:
                written = f.read()
            self.assertIn("def answer", written)
            self.assertIn("# version: 1", written)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_single_fenced_block_can_be_used_as_target_content(self):
        response = """
        ```python
        VALUE = 1
        ```
        """
        self.assertEqual(LLMAgent._extract_codex_file_content(response, "config.py"), "VALUE = 1")


if __name__ == "__main__":
    unittest.main()
