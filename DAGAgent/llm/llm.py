from typing import List, Union
from abc import ABC
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI


class LLM(ABC):
    def __init__(self, api_key: str, api_base: str,  deployment_name: str, max_tokens: int = 1024, temperature: float = 0.0):
        self.llm = ChatOpenAI(
            openai_api_key=api_key,
            openai_api_base=api_base,
            openai_api_version=api_version,
            deployment_name=deployment_name,
            max_tokens=max_tokens,
            temperature=temperature
        )

        self.prompt_tokens = 0
        self.completion_tokens = 0
        
    def chat(self, messages: List[SystemMessage | HumanMessage]) -> str:
        response = self.llm.invoke(messages)

        self.prompt_tokens = response.usage_metadata["input_tokens"]
        self.completion_tokens = response.usage_metadata["output_tokens"]

        return response.content