from abc import ABC
import json
import time
from typing import Any, Dict, List

from openai import OpenAI

from MetaFlow.utils.log import get_logger

logger = get_logger()


class Qwen(ABC):
    def __init__(self, api_key: str, api_base: str, model: str, max_tokens: int = 10240, temperature: float = 0.0):
        self.client = OpenAI(
            api_key=api_key,
            base_url=api_base,
        )
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_tokens = 0
        self.completion_tokens = 0
        
    def chat(self, messages: List[Dict[str, Any]]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
            
            temperature=self.temperature,
        )

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

        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
            
            temperature=self.temperature,
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
        is_tool_call = False

        while current_iteration < max_iterations:
            current_iteration += 1

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    tool_choice="auto",  
                     
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                
                if hasattr(response, 'usage'):
                    self.prompt_tokens += response.usage.prompt_tokens
                    self.completion_tokens += response.usage.completion_tokens
                    logger.info(f"Token Usage - Prompt: {response.usage.prompt_tokens}, Completion: {response.usage.completion_tokens}")
                
                response_message = response.choices[0].message
                
                # IMPORTANT: Qwen requires the tool call message to be appended even if content is None/empty
                # Ensure content is at least an empty string if None
                if response_message.content is None:
                    response_message.content = ""
                
                # Append the assistant's response (which may include tool calls)
                messages.append(response_message)
                
                if hasattr(response_message, 'tool_calls') and response_message.tool_calls:
                    logger.info(f"Tool Calls: {[tc.function.name for tc in response_message.tool_calls]}")
                    
                    # Execute each tool call
                    for tool_call in response_message.tool_calls:
                        function_name = tool_call.function.name
                        function_to_call = available_functions.get(function_name)
                        is_tool_call = True
                        
                        if function_to_call:
                            try:
                                function_args = json.loads(tool_call.function.arguments)
                                logger.info(f"Execute tool {function_name} with args: {function_args}")
                                
                                # Execute the tool function
                                function_response = function_to_call(**function_args)
                                execution_result = str(function_response)

                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "name": function_name,
                                    "content": execution_result
                                })
                            except Exception as e:
                                error_msg = f"Tool {function_name} execution error: {e}"
                                logger.error(error_msg)
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "name": function_name,
                                    "content": error_msg
                                })
                        else:
                            error_msg = f"Tool '{function_name}' not found."
                            logger.error(error_msg)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": function_name,
                                "content": error_msg
                            })
                else:
                    # No tool calls, this is the final response or a direct text response
                    break
            except Exception as e:
                error_msg = f"LLM call error: {e}"
                logger.error(error_msg)
                time.sleep(10)
                
        # Final summarization step: Generate complete output based on the conversation history.
        if is_tool_call:
            try:
                summary_instruction = {
                    "role": "system",
                    "content": """
    Based on the tool executions and conversation history, provide a complete response that includes:
    1. A brief summary of what was accomplished (<thinking> section)
    2. A clear description of the work done (<output> section)  
    3. Any document updates if needed (<document_action> section)

    Ensure your response follows the exact format requirements with all required sections.
    """
                }
                messages.append(summary_instruction)
                
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.error(f"Error during final summarization: {e}")
                return "Error generating summary."

        # Return the content of the last message if it's from the assistant
        if messages and hasattr(messages[-1], 'content'):
            return messages[-1].content
        elif messages and isinstance(messages[-1], dict):
            return messages[-1].get("content", "")
            
        return ""
