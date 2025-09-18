from typing import List, Union
from openai import AzureOpenAI
from abc import ABC
from DAGAgent.utils.state import Message


class LLM(ABC):
    def __init__(self, api_key: str, api_base: str, api_version: str, deployment_name: str, max_tokens: int = 1024, temperature: float = 0.0):
        self.client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=api_base
        )
        self.deployment_name = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_tokens = 0
        self.completion_tokens = 0
        
    def chat(self, messages: List[Message]) -> str:
        response = self.client.chat.completions.create(
            model=self.deployment_name,
            max_tokens=self.max_tokens,
            messages=messages,
            timeout=30,
            temperature=self.temperature
        )

        self.prompt_tokens = response.usage.prompt_tokens
        self.completion_tokens = response.usage.completion_tokens

        return response.choices[0].message.content