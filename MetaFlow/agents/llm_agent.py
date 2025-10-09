import re
from typing import List, Dict, Union

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.utils.state import GeneralState, Message
from MetaFlow.prompt.system_prompt import SYSTEM_PROMPT, AGENT_PROMPT, AGENT_DETAILS
from MetaFlow.config import Config


class LLMAgent(BaseAgent):
    """
    A concrete base agent for all agents that primarily rely on an LLM to generate a response.
    It handles the logic of formatting prompts, calling the LLM, and parsing the standard output.
    """
    def __init__(self, agent_name: str, config: Config):
        super().__init__(agent_name, config)

    def _execute_agent(self, state: GeneralState, test_cases: List[str], next_available_agents: List[str]) -> Message:
        """
        A generic implementation that executes the agent's logic by calling the LLM.
        """
        task_description = f"User Task: {state.task}"

        inputs = self.get_prompt(
            sys_prompt=self.get_system_prompt(), 
            agent_prompt=self.get_agent_prompt(self.agent_name),
            prompt=task_description, 
            next_available_agents=next_available_agents, 
            agent_details=AGENT_DETAILS,
        )

        response_text = self.llm.chat(inputs)

        thinking = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        output = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)

        return Message(
            role=self.agent_name,
            thinking=thinking.group(1).strip() if thinking else "",
            output=output.group(1).strip() if output else response_text,
        )