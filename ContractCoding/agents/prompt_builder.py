from __future__ import annotations

from typing import Dict, List, Sequence

from ContractCoding.agents.prompts import AGENT_DETAILS, CORE_SYSTEM_PROMPT, build_system_prompt


class AgentPromptBuilder:
    def __init__(self, agent_name: str, agent_prompt: str, system_prompt: str = CORE_SYSTEM_PROMPT):
        self.agent_name = agent_name
        self.agent_prompt = agent_prompt
        self.system_prompt = system_prompt

    def build(
        self,
        task_description: str,
        current_task: str,
        next_available_agents: Sequence[str],
    ) -> List[Dict[str, str]]:
        system_prompt = build_system_prompt(self.agent_name, current_task, self.system_prompt)
        if self.agent_name == "Project_Manager":
            agent_summaries = ", ".join(self._describe_agent(agent_name) for agent_name in next_available_agents)
            system_prompt = f"{system_prompt}\n                # Available Agents: {agent_summaries}\n            "

        return [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": f"# Your Role Guideline:\n {self.agent_prompt}"},
            {
                "role": "user",
                "content": (
                    "# User Overall Task\n"
                    f"{task_description}\n\n"
                    "# Current Task\n"
                    f"{current_task}\n"
                ),
            },
        ]

    @staticmethod
    def _describe_agent(agent_name: str) -> str:
        return f"{agent_name}: {AGENT_DETAILS.get(agent_name, '')}"
