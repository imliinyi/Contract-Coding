"""SkillLibrary behavior tests."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from typing import List, Optional


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ContractCoding.memory.ledgers import FailedHypothesis
from ContractCoding.memory.skills import DEFAULT_SKILLS_ROOT, default_skill_library, load_skill_cards


def _packet(
    *,
    title: str = "implement feature",
    goal: str = "implement a small feature",
    output_format: str = "python",
    boundaries: Optional[List[str]] = None,
    tool_whitelist: Optional[List[str]] = None,
    capsule_dependencies: Optional[List[str]] = None,
    prior_failures: Optional[List[FailedHypothesis]] = None,
    artifacts: Optional[List[object]] = None,
    attempts: int = 0,
) -> SimpleNamespace:
    task = SimpleNamespace(
        task_id="t1",
        title=title,
        goal=goal,
        output_format=output_format,
        boundaries=boundaries or [],
        tool_whitelist=tool_whitelist or [],
        capsule_dependencies=capsule_dependencies or [],
        attempts=attempts,
    )
    return SimpleNamespace(
        task=task,
        prior_failures=prior_failures or [],
        artifacts=artifacts or [],
    )


class SkillLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.library = default_skill_library()

    def _ids_for(self, role: str, packet: SimpleNamespace) -> set[str]:
        return {card.skill_id for card in self.library.list_for(role, packet)}

    def test_capsule_tasks_pull_boundary_card_for_downstream_roles(self) -> None:
        packet = _packet(capsule_dependencies=["domain:add"])

        self.assertIn("inspector_pull_discipline", self._ids_for("planner", packet))
        self.assertIn("inspector_pull_discipline", self._ids_for("implementer", packet))
        self.assertIn("implementer_capsule_compliance", self._ids_for("implementer", packet))

    def test_repair_tasks_pull_localization_and_failure_memory(self) -> None:
        failure = FailedHypothesis(
            fingerprint="fp:test",
            what_was_tried="changed parser broadly",
            why_failed="same failing test remained",
            related_task_ids=["t1"],
        )
        packet = _packet(
            title="fix failing parser test",
            goal="fix regression from traceback",
            prior_failures=[failure],
            attempts=1,
        )

        implementer_ids = self._ids_for("implementer", packet)
        self.assertIn("planner_repair_localization", implementer_ids)
        self.assertIn("implementer_failure_memory", implementer_ids)

    def test_artifact_review_pulls_evidence_and_test_quality_cards(self) -> None:
        packet = _packet(artifacts=[object()])

        reviewer_ids = self._ids_for("reviewer", packet)
        judge_ids = self._ids_for("judge", packet)
        self.assertIn("reviewer_evidence_first", reviewer_ids)
        self.assertIn("reviewer_risk_map", reviewer_ids)
        self.assertIn("reviewer_test_quality", reviewer_ids)
        self.assertIn("judge_diff_evidence", judge_ids)

    def test_role_specific_skills_do_not_bleed_between_roles(self) -> None:
        packet = _packet(artifacts=[object()])

        planner_ids = self._ids_for("planner", packet)
        reviewer_ids = self._ids_for("reviewer", packet)

        self.assertIn("planner_repo_survey", planner_ids)
        self.assertNotIn("reviewer_evidence_first", planner_ids)
        self.assertIn("reviewer_evidence_first", reviewer_ids)
        self.assertNotIn("planner_repo_survey", reviewer_ids)

    def test_tool_safety_only_applies_when_task_declares_tools(self) -> None:
        unconstrained = _packet()
        constrained = _packet(tool_whitelist=["read", "write_workspace_text"])

        self.assertNotIn("implementer_tool_safety", self._ids_for("implementer", unconstrained))
        self.assertIn("implementer_tool_safety", self._ids_for("implementer", constrained))

    def test_default_library_loads_markdown_skill_folders(self) -> None:
        cards = load_skill_cards(DEFAULT_SKILLS_ROOT)
        ids = {card.skill_id for card in cards}

        self.assertGreaterEqual(len(cards), 10)
        self.assertIn("planner_repo_survey", ids)
        self.assertNotIn("skill_authoring", ids)
        card = self.library.get("planner_repo_survey")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertIn("SKILL.md", card.source_path)
        self.assertIn("compact repo map", card.prompt_fragment)


if __name__ == "__main__":
    unittest.main()
