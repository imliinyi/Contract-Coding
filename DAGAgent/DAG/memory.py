from typing import List
from DAGAgent.utils.state import Message
from DAGAgent.llm.llm import LLM
from DAGAgent.config import Config


class MemoryManager:
    def __init__(self, config: Config, memory_window: int):
        self.llm = LLM(
            deployment_name=config.OPENAI_API_DEPLOYMENT_NAME,
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_API_BASE_URL,
            max_tokens=config.OPENAI_API_MAX_TOKENS,
            temperature=config.OPENAI_API_TEMPERATURE,
        )
        self.memory_window = memory_window

    def add_message(self, message: Message):
        self.memory.append(message)
        if len(self.memory) > self.memory_window:
            self.memory.pop(0)
        