from abc import ABC
import json
import os
import subprocess
import time
from typing import Any, Dict, List

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
    def __init__(
        self,
        api_key: str,
        api_base: str,
        deployment_name: str,
        max_tokens: int = 10240,
        temperature: float = 0.0,
        backend: str = "openai",
        codex_cli_command: str = "codex exec --sandbox read-only --ask-for-approval never -",
        codex_cli_workdir: str = ".",
        codex_cli_timeout: int = 300,
        codex_cli_max_output_chars: int = 200000,
        codex_cli_read_only: bool = True,
    ):
        self.backend = (backend or "openai").strip().lower()
        self.deployment_name = deployment_name
        self.model = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_tokens = 0
        self.completion_tokens = 0

        self.codex_cli_command = codex_cli_command
        self.codex_cli_workdir = codex_cli_workdir or "."
        self.codex_cli_timeout = codex_cli_timeout
        self.codex_cli_max_output_chars = codex_cli_max_output_chars
        self.codex_cli_read_only = codex_cli_read_only

        self.client = None
        if self.backend == "openai":
            from openai import OpenAI

            self.client = OpenAI(
                api_key=api_key,
                base_url=_normalize_openai_base_url(api_base),
            )
        elif self.backend != "codex_cli":
            raise ValueError("MODEL_BACKEND must be one of: openai, codex_cli")

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        if self.backend == "codex_cli":
            return self._chat_with_codex_cli(messages)
        return self._chat_with_openai(messages)

    def _chat_with_openai(self, messages: List[Dict[str, Any]]) -> str:
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

    def _chat_with_codex_cli(self, messages: List[Dict[str, Any]]) -> str:
        prompt = self._messages_to_codex_prompt(messages)
        if self.codex_cli_read_only:
            lowered = (self.codex_cli_command or "").lower()
            if "read-only" not in lowered and "readonly" not in lowered:
                logger.warning("CODEX_CLI_READ_ONLY is enabled, but CODEX_CLI_COMMAND does not visibly contain a read-only sandbox flag.")

        workdir = os.path.abspath(self.codex_cli_workdir)
        try:
            result = subprocess.run(
                self.codex_cli_command,
                input=prompt,
                cwd=workdir if os.path.isdir(workdir) else None,
                shell=True,
                text=True,
                capture_output=True,
                timeout=self.codex_cli_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Codex CLI timed out after {self.codex_cli_timeout}s") from exc

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(
                f"Codex CLI failed with exit code {result.returncode}.\nSTDERR:\n{stderr}\nSTDOUT:\n{stdout[:4000]}"
            )

        if stderr:
            logger.info(f"Codex CLI stderr: {stderr[:4000]}")
        if len(stdout) > self.codex_cli_max_output_chars:
            logger.warning("Codex CLI output exceeded CODEX_CLI_MAX_OUTPUT_CHARS and was truncated.")
            stdout = stdout[: self.codex_cli_max_output_chars]
        return stdout

    def _messages_to_codex_prompt(self, messages: List[Dict[str, Any]]) -> str:
        parts = [
            "You are running as the model backend for ContractCoding.",
            "You MUST NOT write, edit, delete, or move files directly.",
            "You are expected to run in a read-only Codex CLI sandbox.",
            "Return generated code and document updates in text only.",
            "If implementation code is needed, use this exact envelope:",
            "<file_write path=\"relative/path.py\">",
            "```python",
            "# code here",
            "```",
            "</file_write>",
            "Also include the required <thinking>, <output>, and <document_action> blocks when the agent protocol asks for them.",
            "Do not claim that files were written by Codex CLI itself.",
        ]
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if isinstance(content, list):
                content = str(content)
            parts.append(f"\n--- {role.upper()} MESSAGE ---\n{content}")
        return "\n".join(parts)

    def chat_with_image(self, messages: List[Dict[str, Any]], images: List[str]) -> str:
        if self.backend == "codex_cli":
            if images:
                messages = list(messages) + [
                    {
                        "role": "system",
                        "content": "Image inputs are not supported by the Codex CLI backend in this framework. Continue using the text context only.",
                    }
                ]
            return self.chat(messages)

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
        if self.backend == "codex_cli":
            tool_names = []
            for tool in tools:
                if hasattr(tool, "__name__"):
                    tool_names.append(tool.__name__)
                elif isinstance(tool, dict) and "function" in tool:
                    tool_names.append(tool["function"].get("name", "unknown_tool"))
            guidance = {
                "role": "system",
                "content": (
                    "Native tool calling is disabled for the Codex CLI backend. "
                    "The CLI must remain read-only. Return code in <file_write path=\"...\">...</file_write> envelopes "
                    "and return document changes in <document_action>. Available framework tools that will be handled outside the CLI: "
                    + ", ".join(tool_names)
                ),
            }
            return self.chat(list(messages) + [guidance])

        tool_schemas = []
        available_functions = {}

        for tool in tools:
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

                    for tool_call in response_message.tool_calls:
                        function_name = tool_call.function.name
                        function_to_call = available_functions.get(function_name)

                        if function_to_call:
                            try:
                                function_args = json.loads(tool_call.function.arguments)
                                logger.info(f"Execute tool {function_name} with args: {function_args}")
                                function_response = function_to_call(**function_args)
                                execution_result = str(function_response)

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
                    messages.append({"role": response_message.role, "content": response_message.content})
                    break
            except Exception as e:
                error_msg = f"LLM call error: {e}"
                logger.error(error_msg)
                time.sleep(30 if _is_token_limit_error(e) else 10)

        if current_iteration >= max_iterations:
            logger.warning(f"Max tool call iterations reached: {max_iterations}")

        return last_content
