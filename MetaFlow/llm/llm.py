from re import S
from typing import Dict, List, Union
from abc import ABC

from openai import OpenAI
# from langchain import hub
# from langchain_core.tools import BaseTool
# from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
# from langchain.agents import AgentExecutor, create_react_agent

from MetaFlow.utils.state import Message


class LLM(ABC):
    def __init__(self, api_key: str, api_base: str,  deployment_name: str, max_tokens: int = 1024, temperature: float = 0.0):
        # self.llm = ChatOpenAI(
        #     api_key=api_key,
        #     base_url=api_base,
        #     model=deployment_name,
        #     max_tokens=max_tokens,
        #     temperature=temperature
        # )
        self.client = OpenAI(
            api_key=api_key,
            base_url=api_base,
        )
        # self.prompt = hub.pull("hwchase17/react")

        self.deployment_name = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_tokens = 0
        self.completion_tokens = 0
        
    def chat(self, messages: Dict) -> str:
        response = self.client.chat.completions.create(
            model=self.deployment_name,
            max_tokens=self.max_tokens,
            messages=messages,
            timeout=30,
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )

        self.prompt_tokens = response.usage.prompt_tokens
        self.completion_tokens = response.usage.completion_tokens

        return response.choices[0].message.content

    # def chat_with_tools(self, messages: List[Message], tools: List[BaseTool]) -> str:
    #     agent = create_react_agent(self.llm, tools, self.prompt)
    #     agent_executor = AgentExecutor(agent=agent, tools=tools)

    #     response = agent_executor.invoke({"input": messages})

    #     self.prompt_tokens = response.usage.prompt_tokens
    #     self.completion_tokens = response.usage.completion_tokens

    #     return response.choices[0].message.content
