import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ContractCoding.memory.audit import audit_contract_interfaces
from ContractCoding.memory.contract import ContractParseError, parse_contract_kernel
from ContractCoding.memory.document import DocumentManager
from ContractCoding.orchestration.traverser import GraphTraverser
from ContractCoding.utils.state import GeneralState


CONTRACT = """
## Product Requirement Document (PRD)

### 1.1 Project Overview
Demo.

## Technical Architecture Document (System Design)

### 2.1 Directory Structure
```text
workspace/
├── models.py
├── service.py
└── main.py
```

### 2.3 Dependency Relationships(MUST):
- `service.py` depends on `models.py`
- `main.py` depends on `service.py`

### 2.4 Symbolic API Specifications
**File:** `models.py`
* **Class:** `Player`
    * **Attributes:**
        * `name: str` - display name
    * **Methods:**
        * `def move(self, dx: int, dy: int) -> None:`
            + Docstring: Move the player.
    * **Owner:** Backend_Engineer
    * **Version:** 1
    * **Status:** VERIFIED

**File:** `service.py`
* **Class:** `GameService`
    * **Methods:**
        * `def start(self, player: Player) -> bool:`
            + Docstring: Start game.
    * **Owner:** Backend_Engineer
    * **Version:** 1
    * **Status:** TODO

**File:** `main.py`
* **Function:** `main`
    * `def main() -> None:`
* **Owner:** Frontend_Engineer
* **Version:** 1
* **Status:** TODO
"""


class ContractKernelTests(unittest.TestCase):
    def test_contract_markdown_parses_to_kernel(self):
        kernel = parse_contract_kernel(CONTRACT)
        self.assertEqual([f.path for f in kernel.files], ["models.py", "service.py", "main.py"])
        self.assertEqual(kernel.by_path()["service.py"].owner, "Backend_Engineer")
        self.assertEqual(kernel.dependencies["service.py"], ["models.py"])
        self.assertEqual(kernel.by_path()["models.py"].classes[0].methods[0].name, "move")

    def test_missing_required_fields_raise_parse_error(self):
        bad_contract = CONTRACT.replace("* **Owner:** Backend_Engineer\n", "", 1)
        with self.assertRaises(ContractParseError) as ctx:
            parse_contract_kernel(bad_contract)
        self.assertIn("Owner", ctx.exception.issues[0].field)
        self.assertEqual(ctx.exception.issues[0].path, "models.py")


class AuditTests(unittest.TestCase):
    def test_ast_audit_detects_signature_mismatch_and_placeholder(self):
        kernel = parse_contract_kernel(CONTRACT)
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w", encoding="utf-8") as f:
                f.write("class Player:\n    def move(self, direction: str) -> None:\n        pass\n")
            with open(os.path.join(tmp, "service.py"), "w", encoding="utf-8") as f:
                f.write("class GameService:\n    def start(self, player) -> bool:\n        return True\n")
            with open(os.path.join(tmp, "main.py"), "w", encoding="utf-8") as f:
                f.write("def main() -> None:\n    return None\n")

            issues = audit_contract_interfaces(kernel, tmp)

        kinds = {issue.kind for issue in issues}
        self.assertIn("parameter_mismatch", kinds)
        self.assertIn("placeholder_pass", kinds)


class DependencySchedulingTests(unittest.TestCase):
    def test_dependency_blocks_unverified_downstream_task(self):
        doc = DocumentManager()
        doc._document = CONTRACT.replace("* **Status:** VERIFIED", "* **Status:** TODO", 1)
        config = SimpleNamespace(MAX_LAYERS=3, MAX_WORKERS=2, TERMINATION_POLICY="all", WORKSPACE_DIR=".", LOG_PATH="./test.log")
        traverser = GraphTraverser(config, Mock(), Mock(), doc)
        state = GeneralState(task="demo")

        scheduled = traverser._schedule_from_contract(doc.get(), {"Project_Manager": [state]})

        self.assertIn("Backend_Engineer", scheduled)
        task_messages = [s.sub_task for s in scheduled["Backend_Engineer"]]
        self.assertTrue(any("models.py" in msg for msg in task_messages))
        self.assertFalse(any("service.py" in msg for msg in task_messages))


if __name__ == "__main__":
    unittest.main()
