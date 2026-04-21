from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Dict, List

from ContractCoding.memory.contract_state import ModuleTeamPlan
from ContractCoding.review.result import ReviewPacket
from ContractCoding.utils.state import GeneralState


REVIEW_AGENTS = ("Critic", "Code_Reviewer")


class ReviewGate:
    def build_packet(self, plan: ModuleTeamPlan) -> ReviewPacket:
        files = [str(task.get("file", "")).strip() for task in plan.review_tasks if task.get("file")]
        return ReviewPacket(
            module_name=plan.name,
            files=files,
            module_dependencies=list(plan.module_dependencies),
        )

    def schedule_reviews(
        self,
        plan: ModuleTeamPlan,
        base_state: GeneralState,
    ) -> Dict[str, List[GeneralState]]:
        if plan.ready_tasks or not plan.review_tasks:
            return {}

        packet = self.build_packet(plan)
        review_states: DefaultDict[str, List[GeneralState]] = defaultdict(list)
        for agent_name in REVIEW_AGENTS:
            review_state = base_state.model_copy()
            review_state.sub_task = packet.render()
            review_states[agent_name].append(review_state)
        return dict(review_states)
