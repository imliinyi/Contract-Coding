import json
from typing import Any, Dict, List, Union, Callable
from abc import ABC
from openai import AzureOpenAI

from MetaFlow.tools.code_tool import run_code


class LLM(ABC):
    def __init__(self, api_key: str, api_base: str,  deployment_name: str, max_tokens: int = 1024, temperature: float = 0.0):
        # self.llm = ChatOpenAI(
        #     api_key=api_key,
        #     base_url=api_base,
        #     model=deployment_name,
        #     max_tokens=max_tokens,
        #     temperature=temperature
        # )
        self.client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-03-01-preview",
            base_url=api_base,
        )

        self.deployment_name = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_tokens = 0
        self.completion_tokens = 0
        
    def chat(self, messages: Any) -> str:
        response = self.client.chat.completions.create(
            model=self.deployment_name,
            max_tokens=self.max_tokens,
            messages=messages,
            timeout=30,
            temperature=self.temperature,
            # response_format={"type": "json_object"},
            extra_headers={"X-TT-LOGID": ""},
        )

        self.prompt_tokens = response.usage.prompt_tokens
        self.completion_tokens = response.usage.completion_tokens

        return response.choices[0].message.content

    def chat_with_tools(self, messages: List[Dict[str, str]], tools: List[Callable]) -> str:
        tool_schemas = [tool.openai_schema for tool in tools]
        
        response = self.client.chat.completions.create(
            model=self.deployment_name,
            messages=messages,
            tools=tool_schemas,
            tool_choice="auto",
        )
        
        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            messages.append(response_message)
            available_functions = {tool.__name__: tool for tool in tools}
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_to_call = available_functions[function_name]
                function_args = json.loads(tool_call.function.arguments)
                function_response = function_to_call(**function_args)
                messages.append(
                    {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response,
                    }
                )
            second_response = self.client.chat.completions.create(
                model=self.deployment_name,
                messages=messages,
            )
            return second_response.choices[0].message.content
        
        return response_message.content
