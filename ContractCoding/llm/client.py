from abc import ABC
import json
import time
from typing import Any, Dict, List

from openai import OpenAI

from ContractCoding.utils.log import get_logger

logger = get_logger()


def _normalize_openai_base_url(api_base: str) -> str:
    base = (api_base or "").strip()
    if not base:
        return base

    suffixes = [
        "/chat/completions",
        "/v1/chat/completions",
    ]
    for s in suffixes:
        if base.endswith(s):
            base = base[: -len(s)]
            break

    return base.rstrip("/")


def _is_token_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(
        s in msg
        for s in (
            "maximum context length",
            "context_length_exceeded",
            "too many tokens",
            "token limit",
            "exceeds the maximum context",
            "maximum tokens",
            "reduce the length",
        )
    )


class LLM(ABC):
    def __init__(self, api_key: str, api_base: str,  deployment_name: str, max_tokens: int = 10240, temperature: float = 0.0):
        self.client = OpenAI(
            api_key=api_key,
            base_url=_normalize_openai_base_url(api_base),
        )
        self.deployment_name = deployment_name
        self.model = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_tokens = 0
        self.completion_tokens = 0
        
    def chat(self, messages: List[Dict[str, Any]]) -> str:
        retry = 0
        while True:
            retry += 1
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    timeout=30,
                    temperature=self.temperature,
                )
                break
            except Exception as e:
                sleep_s = 30 if _is_token_limit_error(e) else 10
                logger.error(f"LLM call error: {e}")
                time.sleep(sleep_s)
                if retry >= 3:
                    raise

        self.prompt_tokens = response.usage.prompt_tokens
        self.completion_tokens = response.usage.completion_tokens

        return response.choices[0].message.content

    def chat_with_image(self, messages: List[Dict[str, Any]], images: List[str]) -> str:
        if not images:
            return self.chat(messages)

        for image in images:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image}"
                        }
                    }
                ]
            })

        retry = 0
        while True:
            retry += 1
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    timeout=60,
                    temperature=self.temperature,
                )
                break
            except Exception as e:
                sleep_s = 30 if _is_token_limit_error(e) else 10
                logger.error(f"LLM call error: {e}")
                time.sleep(sleep_s)
                if retry >= 3:
                    raise

        self.prompt_tokens = response.usage.prompt_tokens
        self.completion_tokens = response.usage.completion_tokens

        return response.choices[0].message.content

    def chat_with_tools(self, messages: List[Dict[str, Any]], tools: List[Any]) -> str:
        """
        Use OpenAI native tool calling mechanism to handle chat and tool invocations.
        This method supports nested tool calls and ensures tool execution results are passed correctly to the model.
        """
        tool_schemas = []
        available_functions = {}
        
        for tool in tools:
            # Check if the tool has openai_schema attribute
            if hasattr(tool, 'openai_schema'):
                tool_schemas.append(tool.openai_schema)
                available_functions[tool.__name__] = tool
            elif isinstance(tool, dict) and 'function' in tool:
                tool_schemas.append(tool)
                if 'implementation' in tool:
                    available_functions[tool['function']['name']] = tool['implementation']

        max_iterations = 10
        current_iteration = 0
        last_content = ""

        while current_iteration < max_iterations:
            current_iteration += 1

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    tool_choice="auto",  
                    timeout=60, 
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                
                if hasattr(response, 'usage'):
                    self.prompt_tokens += response.usage.prompt_tokens
                    self.completion_tokens += response.usage.completion_tokens
                    logger.info(f"Token Usage - Prompt: {response.usage.prompt_tokens}, Completion: {response.usage.completion_tokens}")
                
                response_message = response.choices[0].message

                if response_message.content is None:
                    response_message.content = ""
                last_content = response_message.content
                
                if hasattr(response_message, 'tool_calls') and response_message.tool_calls:
                    logger.info(f"Tool Calls: {[tc.function.name for tc in response_message.tool_calls]}")
                    
                    messages.append({
                        "role": response_message.role,
                        "content": response_message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in response_message.tool_calls
                        ]
                    })
                    
                    # Execute each tool call
                    for tool_call in response_message.tool_calls:
                        function_name = tool_call.function.name
                        function_to_call = available_functions.get(function_name)
                        
                        if function_to_call:
                            try:
                                function_args = json.loads(tool_call.function.arguments)
                                logger.info(f"Execute tool {function_name} with args: {function_args}")
                                
                                # Execute the tool function
                                function_response = function_to_call(**function_args)
                                execution_result = str(function_response)
                                # logger.info(f"Tool {function_name} execution result: {execution_result[:200]}..." if len(execution_result) > 200 else f"Tool {function_name} execution result: {execution_result}")

                                messages.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": function_name,
                                    "content": execution_result
                                })
                            except Exception as e:
                                error_msg = f"Tool {function_name} execution error: {e}"
                                logger.error(error_msg)
                                messages.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": function_name,
                                    "content": error_msg
                                })
                        else:
                            error_msg = f"Tool '{function_name}' not found."
                            logger.error(error_msg)
                            messages.append({
                                "tool_call_id": tool_call.id,
                                "role": "tool",
                                "name": function_name,
                                "content": error_msg
                            })
                else:
                    # No more tool calls, return the model's final response content
                    messages.append({"role": response_message.role, "content": response_message.content})
                    break
            except Exception as e:
                error_msg = f"LLM call error: {e}"
                logger.error(error_msg)
                time.sleep(30 if _is_token_limit_error(e) else 10)

        if current_iteration >= max_iterations:
            logger.warning(f"Max tool call iterations reached: {max_iterations}")

        return last_content
