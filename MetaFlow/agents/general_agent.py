from typing import List

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.llm.llm import LLM
from MetaFlow.config import Config
from MetaFlow.utils.state import Message, GeneralState



class PlannerAgent(BaseAgent):
    def __init__(self, name: str, config: Config):
        super().__init__(name, config)
        self.system_prompt = self.get_system_prompt()
        self.agent_prompt = self.get_agent_prompt(self.agent_name)
