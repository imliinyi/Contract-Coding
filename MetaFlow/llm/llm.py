from abc import ABC
import json
import re
from typing import Any, Callable, Dict, List, Union

from openai import AzureOpenAI

from MetaFlow.flow.decision_space import logger
from MetaFlow.tools.code_tool import run_code

# logger = logging.getLogger(__name__)

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

    def chat_with_tools(self, messages: List[Dict[str, str]], tools: List[Callable]) -> str:
        tool_schemas = [tool.openai_schema for tool in tools]
        available_functions = {tool.__name__: tool for tool in tools}
        
        max_iterations = 10
        current_iteration = 0

        while current_iteration < max_iterations:
            current_iteration += 1

            # First API call to get the model's response
            try:
                response = self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                )
                response_message = response.choices[0].message
                response_content = response_message.content or ""
                logger.info(f"LLM response content: {response_content}")
            except Exception as e:
                logger.error(f"Error in LLM call during tool use loop: {e}")
                return f'<thinking>LLM API call failed.</thinking><output>Error: {e}</output><next_agents>["END"]</next_agents><task_requirements>{{}}</task_requirements>'

            # Append the assistant's response to the message history
            messages.append(response_message)

            # Primary Logic: Parse the <output> tag for a tool call
            output_match = re.search(r'<output>(.*?)</output>', response_content, re.DOTALL)
            if not output_match:
                return response_content 

            output_text = output_match.group(1).strip()
            try:
                parsed_json = json.loads(output_text)
            except (json.JSONDecodeError, TypeError):
                return response_content 

            tool_calls_to_execute = []
            if isinstance(parsed_json, dict) and parsed_json.get("tool_name") == "multi_tool_use.parallel":
                tool_uses = parsed_json.get("parameters", {}).get("tool_uses", [])
                for tool_use in tool_uses:
                    tool_calls_to_execute.append(tool_use)
            elif isinstance(parsed_json, dict) and 'tool_name' in parsed_json and 'parameters' in parsed_json:
                parsed_json['recipient_name'] = parsed_json['tool_name']
                tool_calls_to_execute.append(parsed_json)

            # If there are tools to execute, run them and continue the loop
            if tool_calls_to_execute:
                all_responses_summary = []
                for tool_call in tool_calls_to_execute:
                    function_name_raw = tool_call.get('recipient_name', '') or tool_call.get('tool_name', '')
                    function_name = function_name_raw.split('.')[-1]
                    function_to_call = available_functions.get(function_name)
                    
                    if function_to_call:
                        function_args = tool_call.get('parameters', {})
                        try:
                            logger.info(f"Executing tool {function_name} with args {function_args}")
                            function_response = function_to_call(**function_args)
                            logger.info(f"Function response: {function_response}")
                            all_responses_summary.append(f"- Tool '{function_name}' executed successfully. Result: {function_response}")
                        except Exception as e:
                            error_message = f"Error executing tool {function_name}: {e}"
                            logger.error(error_message)
                            all_responses_summary.append(f"- Tool '{function_name}' failed. Error: {error_message}")
                    else:
                        all_responses_summary.append(f"- Tool '{function_name}' not found.")

                # Create a single summary message for the observation
                final_summary = "\n".join(all_responses_summary)
                tool_response_message = f"""<thinking>I have executed the requested tool(s). Here are the results:\n{final_summary}\nNow I will analyze these results and decide the next step.</thinking><output>Tool execution(s) completed.</output>"""
                messages.append({"role": "assistant", "content": tool_response_message})
                continue # Go to the next iteration of the loop
            
            # If there was a parsable JSON but it wasn't a valid tool call, it's a final answer
            else:
                return response_content
        
        # If the loop finishes due to max_iterations
        logger.warning("Reached maximum tool call iterations.")
        return f'<thinking>Reached maximum tool call iterations ({max_iterations}).</thinking><output>The task may not be fully complete.</output><next_agents>["END"]</next_agents><task_requirements>{{}}</task_requirements>'
