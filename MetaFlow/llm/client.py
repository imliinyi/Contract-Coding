from abc import ABC
import json
from typing import Any, Callable, Dict, List, Union

from openai import AzureOpenAI

from MetaFlow.utils.log import get_logger

logger = get_logger()


class LLM(ABC):
    def __init__(self, api_key: str, api_base: str,  deployment_name: str, max_tokens: int = 10240, temperature: float = 0.0):
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

        while current_iteration < max_iterations:
            current_iteration += 1

            try:
                response = self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    tool_choice="auto",  
                    timeout=60, 
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    extra_headers={"X-TT-LOGID": ""},
                )
                
                if hasattr(response, 'usage'):
                    self.prompt_tokens += response.usage.prompt_tokens
                    self.completion_tokens += response.usage.completion_tokens
                    logger.info(f"Token Usage - Prompt: {response.usage.prompt_tokens}, Completion: {response.usage.completion_tokens}")
                
                response_message = response.choices[0].message
                
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
                                logger.info(f"Tool {function_name} execution result: {str(function_response)[:200]}..." if len(str(function_response)) > 200 else f"Tool {function_name} execution result: {function_response}")
                                
                                messages.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": function_name,
                                    "content": str(function_response)
                                })
                            except json.JSONDecodeError as e:
                                error_msg = f"Tool {function_name} argument parsing error: {e}"
                                logger.error(error_msg)
                                messages.append({
                                    "tool_call_id": tool_call.id,
                                    "role": "tool",
                                    "name": function_name,
                                    "content": error_msg
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
                            error_msg = f"未找到工具 '{function_name}'"
                            logger.error(error_msg)
                            messages.append({
                                "tool_call_id": tool_call.id,
                                "role": "tool",
                                "name": function_name,
                                "content": error_msg
                            })
                else:
                    content = response_message.content or ""
                    # logger.info(f"Received direct response: {content[:200]}..." if len(content) > 200 else f"Received direct response: {content}")
                    return content
                    
            except Exception as e:
                error_msg = f"LLM call error: {e}"
                logger.error(error_msg)
                # Return a formatted error message to the user
                return f"<thinking>LLM call error.</thinking><output>Error: {str(e)}</output><next_agents>['END']</next_agents><task_requirements>{{}}</task_requirements>"

        logger.warning(f"Max tool call iterations reached: {max_iterations}")
        return f'<thinking>Max tool call iterations reached ({max_iterations}).</thinking><output>Task may not be completed.</output><next_agents>["END"]</next_agents><task_requirements>{{"Critic": "Check if the task is completed."}}</task_requirements>'
