import re
from typing import List, Dict, Union

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.flow.decision_space import logger
from MetaFlow.utils.state import GeneralState, Message
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
        # task_description = f"User Overall Task: {state.task}\nYour Current Sub-Task: {state.sub_task}"
        # prompt = f"""
        #     Pre agent output: {state.message.output}\n
        #     Your Current Sub-Task: {state.sub_task}
        # """
        prompt = f"""
            Your Current Sub-Task: {state.sub_task}
        """
        
        inputs = self.get_prompt(
            task_description=state.task,
            sys_prompt=self.get_system_prompt(), 
            agent_prompt=self.get_agent_prompt(self.agent_name),
            prompt=prompt, 
            next_available_agents=next_available_agents
        )

        response_text = self.llm.chat(inputs)
        logger.info(f"==========LLMAgent {self.agent_name} output: {response_text}")
        # thinking = re.search(r'<thinking>(.*?)</thinking>', response_text, re.DOTALL)
        # output = re.search(r'<output>(.*?)</output>', response_text, re.DOTALL)
        message = self._parse_response(response_text)

        return message