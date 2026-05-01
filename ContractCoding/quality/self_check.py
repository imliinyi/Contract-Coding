"""Item-level deterministic self-checks.

Self-check is deliberately narrower than team/project verification. It only
answers whether a WorkItem produced scoped, importable, non-placeholder
artifacts. Behavior-level tests live in team and final gates.
"""

from __future__ import annotations

from typing import Any, Dict

from ContractCoding.contract.work_item import WorkItem
from ContractCoding.runtime.invariants import InvariantChecker, InvariantResult


class SelfChecker(InvariantChecker):
    def check_item(self, item: WorkItem, payload: Dict[str, Any] | None = None) -> InvariantResult:
        return self.check_self_check(item, payload)

