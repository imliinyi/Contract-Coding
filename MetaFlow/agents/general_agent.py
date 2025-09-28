from typing import List

from MetaFlow.agents.base_agent import BaseAgent
from MetaFlow.llm.llm import LLM
from MetaFlow.config import Config
from MetaFlow.utils.state import Message, GeneralState



class PlannerAgent(BaseAgent):
    def __init__(self, name: str, config: Config):
        super().__init__(name, config)
        self.config = config
        self.llm = LLM(
            api_key=self.config.OPENAI_API_KEY,
            api_base=self.config.OPENAI_API_BASE,
            deployment_name=self.config.OPENAI_DEPLOYMENT_NAME,
            max_tokens=self.config.OPENAI_MAX_TOKENS,
            temperature=self.config.OPENAI_TEMPERATURE,
        )
        self.system_prompt = self.get_system_prompt()
        self.agent_prompt = self.get_agent_prompt(self.agent_name)
