"""OpenAI-compatible LLM backend."""

from __future__ import annotations

import copy
import json
import os
import py_compile
from queue import Empty, Queue
import threading
import time
from typing import Any, Dict, List

from openai import AzureOpenAI, OpenAI

from ContractCoding.llm.base import LLMBackend, LLMResponse, ToolIntent
from ContractCoding.tools.governor import ToolGovernor
from ContractCoding.tools.intent_executor import execute_tool_intents
from ContractCoding.utils.log import get_logger


logger = get_logger()

TERMINAL_TOOLS = {"submit_result", "report_blocker"}


def normalize_openai_base_url(api_base: str) -> str:
    base = (api_base or "").strip()
    if not base:
        return base
    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def normalize_azure_endpoint(api_base: str) -> str:
    base = normalize_openai_base_url(api_base)
    marker = "/openai/deployments/"
    if marker in base:
        base = base.split(marker, 1)[0]
    return base.rstrip("/")


def is_token_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(
        marker in msg
        for marker in (
            "maximum context length",
            "context_length_exceeded",
            "too many tokens",
            "token limit",
            "exceeds the maximum context",
            "maximum tokens",
            "reduce the length",
        )
    )


def is_timeout_error(err: Exception) -> bool:
    name = err.__class__.__name__.lower()
    msg = str(err).lower()
    return "timeout" in name or "timed out" in msg or "timeout" in msg


class OpenAIBackend(LLMBackend):
    name = "openai"
    supports_native_tools = True

    def __init__(
        self,
        api_key: str,
        api_base: str,
        deployment_name: str,
        api_version: str = "",
        max_tokens: int = 10240,
        temperature: float = 0.0,
        tool_approval_mode: str = "auto-edit",
        request_timeout: int = 120,
        tool_timeout: int = 300,
        image_timeout: int = 180,
        tool_loop_timeout: int = 900,
        max_tool_iterations: int = 10,
    ):
        normalized_key = (api_key or "").strip()
        if not normalized_key or normalized_key == "Your OpenAI API Key":
            raise ValueError(
                "OpenAI backend requires API credentials. Set API_KEY (preferred) or OPENAI_API_KEY."
            )
        self.api_version = (api_version or "").strip()
        if self.api_version:
            self.client = AzureOpenAI(
                azure_endpoint=normalize_azure_endpoint(api_base),
                api_key=normalized_key,
                api_version=self.api_version,
            )
        else:
            self.client = OpenAI(api_key=normalized_key, base_url=normalize_openai_base_url(api_base))
        self.model = deployment_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.tool_approval_mode = tool_approval_mode
        self.request_timeout = max(30, int(request_timeout or 120))
        self.tool_timeout = max(60, int(tool_timeout or 300))
        self.image_timeout = max(60, int(image_timeout or 180))
        self.tool_loop_timeout = max(0, int(tool_loop_timeout or 0))
        self.max_tool_iterations = max(1, int(max_tool_iterations or 10))
        self.workspace_dir = "."
        self.allowed_artifacts: List[str] = []
        self.allowed_conflict_keys: List[str] = []
        self.allowed_tools: List[str] = []
        self.repair_diagnostics_text = ""
        self._last_attempts: List[Dict[str, Any]] = []

    def chat(self, messages: List[Dict[str, Any]]) -> LLMResponse:
        response = self._retry_chat(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
            timeout=self.request_timeout,
            temperature=self.temperature,
        )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=response.choices[0].message.content or "",
            backend=self.name,
            raw={"attempts": list(self._last_attempts), "provider_response": response},
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

    def chat_with_image(self, messages: List[Dict[str, Any]], images: List[str]) -> LLMResponse:
        if not images:
            return self.chat(messages)
        image_messages = list(messages)
        for image in images:
            image_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image}"},
                        }
                    ],
                }
            )
        response = self._retry_chat(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=image_messages,
            timeout=self.image_timeout,
            temperature=self.temperature,
        )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=response.choices[0].message.content or "",
            backend=self.name,
            raw={"attempts": list(self._last_attempts), "provider_response": response},
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )

    def chat_with_tools(self, messages: List[Dict[str, Any]], tools: List[Any]) -> LLMResponse:
        tool_schemas = []

        for tool in tools:
            if hasattr(tool, "openai_schema"):
                tool_schemas.append(self._strict_tool_schema(tool.openai_schema))
            elif isinstance(tool, dict) and "function" in tool:
                tool_schemas.append(self._strict_tool_schema(tool))

        current_iteration = 0
        max_iterations = self.max_tool_iterations
        last_content = ""
        prompt_tokens = 0
        completion_tokens = 0
        all_intents: List[ToolIntent] = []
        all_results: List[Dict[str, Any]] = []
        adapter_events: List[Dict[str, Any]] = []
        conversation = [self._openai_tool_policy_message(), *messages]
        started_at = time.perf_counter()
        all_attempts: List[Dict[str, Any]] = []
        infra_failure = False
        failure_kind = ""
        failure_message = ""
        stop_reason = ""
        terminal_result: Dict[str, Any] = {}
        governor = ToolGovernor(
            approval_mode=self.tool_approval_mode,
            allowed_tools=list(getattr(self, "allowed_tools", []) or []),
            allowed_artifacts=list(getattr(self, "allowed_artifacts", []) or []),
            allowed_conflict_keys=list(getattr(self, "allowed_conflict_keys", []) or []),
        )
        observer = self._build_patch_guard()

        while current_iteration < max_iterations:
            current_iteration += 1
            elapsed = time.perf_counter() - started_at
            if self.tool_loop_timeout and elapsed >= self.tool_loop_timeout:
                infra_failure = True
                failure_kind = "timeout"
                stop_reason = "tool_loop_timeout"
                failure_message = f"OpenAI tool loop timed out after {self.tool_loop_timeout} seconds."
                all_attempts.append(
                    {
                        "attempt": len(all_attempts) + 1,
                        "iteration": current_iteration,
                        "timeout": self.tool_loop_timeout,
                        "elapsed_seconds": round(elapsed, 4),
                        "returncode": "timeout",
                        "error_type": "tool_loop_timeout",
                    }
                )
                logger.error(failure_message)
                break
            try:
                response = self._retry_chat(
                    model=self.model,
                    messages=conversation,
                    tools=tool_schemas if tool_schemas else None,
                    tool_choice="auto",
                    timeout=self._remaining_tool_timeout(started_at, elapsed=elapsed),
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    max_retries=2,
                    retry_sleep_seconds=2,
                )
                for attempt in self._last_attempts:
                    record = dict(attempt)
                    record["iteration"] = current_iteration
                    all_attempts.append(record)
            except Exception as exc:
                for attempt in self._last_attempts:
                    record = dict(attempt)
                    record["iteration"] = current_iteration
                    all_attempts.append(record)
                logger.error("LLM call error: %s", exc)
                infra_failure = True
                failure_kind = "timeout" if is_timeout_error(exc) else "provider_error"
                failure_message = str(exc)
                stop_reason = failure_kind
                break

            usage = getattr(response, "usage", None)
            if usage:
                prompt_tokens += getattr(usage, "prompt_tokens", 0)
                completion_tokens += getattr(usage, "completion_tokens", 0)

            response_message = response.choices[0].message
            if response_message.content is None:
                response_message.content = ""
            last_content = response_message.content

            if getattr(response_message, "tool_calls", None):
                logger.info("Tool Calls: %s", [tc.function.name for tc in response_message.tool_calls])
                conversation.append(
                    {
                        "role": response_message.role,
                        "content": response_message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in response_message.tool_calls
                        ],
                    }
                )
                tool_calls = list(response_message.tool_calls)
                intents: List[ToolIntent] = []
                parse_errors: Dict[str, str] = {}
                for tool_call in tool_calls:
                    try:
                        function_args = json.loads(tool_call.function.arguments or "{}")
                        if not isinstance(function_args, dict):
                            raise ValueError("tool arguments must be a JSON object")
                    except Exception as exc:
                        function_args = {}
                        parse_errors[tool_call.id] = f"Tool argument parse error for {tool_call.function.name}: {exc}"
                    intents.append(
                        ToolIntent(
                            name=tool_call.function.name,
                            arguments=function_args,
                            reason="openai_native_tool_call",
                        )
                    )

                all_intents.extend(intents)
                results = execute_tool_intents(
                    intents,
                    tools,
                    governor,
                    tool_execution_observer=observer,
                )
                result_records = [result.to_record() for result in results]
                all_results.extend(result_records)
                adapter_events.append(
                    {
                        "iteration": current_iteration,
                        "event": "contractcoding_tool_results",
                        "tool_results": result_records,
                    }
                )
                terminal = self._terminal_tool_result(intents, result_records)
                if terminal:
                    terminal_result = terminal
                    stop_reason = str(terminal.get("tool_name") or "")
                    last_content = self._terminal_content(terminal)
                    break
                for tool_call, result in zip(tool_calls, results):
                    execution_result = json.dumps(result.to_record(), ensure_ascii=False, indent=2)
                    if tool_call.id in parse_errors:
                        execution_result = parse_errors[tool_call.id]
                    conversation.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": tool_call.function.name,
                            "content": execution_result,
                        }
                    )
            else:
                conversation.append({"role": response_message.role, "content": response_message.content})
                stop_reason = "final_message"
                break

        if current_iteration >= max_iterations:
            logger.warning("Max tool call iterations reached: %s", max_iterations)
            auto_submit = self._auto_submit_allowed_artifacts()
            if auto_submit and not terminal_result:
                terminal_result = auto_submit
                stop_reason = "auto_submit_on_iteration_budget"
                failure_kind = ""
                failure_message = ""
                infra_failure = False
                last_content = self._terminal_content(terminal_result)
            elif not stop_reason:
                stop_reason = "max_tool_iterations"
                infra_failure = True
                failure_kind = failure_kind or "tool_loop_exhausted"
                failure_message = failure_message or f"Max tool call iterations reached: {max_iterations}"

        if infra_failure and not terminal_result and failure_kind == "timeout":
            auto_submit = self._auto_submit_allowed_artifacts()
            if auto_submit:
                terminal_result = auto_submit
                stop_reason = "auto_submit_after_timeout"
                failure_kind = ""
                failure_message = ""
                infra_failure = False
                last_content = self._terminal_content(terminal_result)

        return LLMResponse(
            content=last_content,
            backend=self.name,
            raw={
                "tool_results": all_results,
                "adapter_events": adapter_events,
                "attempts": all_attempts,
                "infra_failure": infra_failure,
                "failure_kind": failure_kind,
                "returncode": "timeout" if failure_kind == "timeout" else ("error" if infra_failure else 0),
                "error": failure_message,
                "stop_reason": stop_reason,
                "terminal_result": terminal_result,
                "tool_calls": len(all_intents),
                "tool_call_count": len(all_intents),
                "tool_iterations": current_iteration,
            },
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tool_intents=all_intents,
        )

    @staticmethod
    def _openai_tool_policy_message() -> Dict[str, str]:
        return {
            "role": "system",
            "content": (
                "ContractCoding executes every tool call through its own policy and validation layer. "
                "Use tools as precise edit intents, not as direct authority. For existing files, first inspect "
                "with read_lines or inspect_symbol, then prefer update_file_lines or replace_symbol for narrow edits. "
                "Use replace_file for scaffold replacement or whole-file regeneration so old trailing "
                "placeholder code is truncated. Use create_file for missing target artifacts; a missing package "
                "directory is not a blocker when the path is in allowed_artifacts. During repairs, do not edit tests unless diagnostics "
                "explicitly say invalid_tests. Public entrypoint modules such as CLI/API must be import-safe: lazy "
                "import downstream package modules inside command handlers so a team workspace can import the "
                "entrypoint before other teams are promoted. If a tool result contains validation_status=rolled_back, "
                "your patch was rejected and the file was restored; read the suggested range and repair again in the "
                "same conversation. If the needed artifact is outside allowed scope, call report_blocker or return "
                "structured blocker JSON instead of editing around it. When the work is complete, call submit_result "
                "with changed files and evidence; this is the worker completion packet. If the contract itself is "
                "ambiguous, report a blocker rather than natural-language success. Treat submit_result and "
                "report_blocker as terminal actions; do not continue making tool calls after them."
            ),
        }

    @staticmethod
    def _terminal_tool_result(intents: List[ToolIntent], result_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        for intent, record in zip(intents, result_records):
            if intent.name not in TERMINAL_TOOLS:
                continue
            payload: Dict[str, Any] = {}
            output = str(record.get("output", "") or "")
            try:
                parsed = json.loads(output) if output.strip() else {}
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except json.JSONDecodeError:
                payload["summary"] = output.strip()
            payload.setdefault("tool_name", intent.name)
            payload.setdefault("allowed", bool(record.get("allowed")))
            payload.setdefault("reason", str(record.get("reason", "") or payload.get("reason", "")))
            payload.setdefault("arguments", dict(intent.arguments))
            return payload
        return {}

    @staticmethod
    def _terminal_content(terminal: Dict[str, Any]) -> str:
        tool_name = str(terminal.get("tool_name") or "")
        if tool_name == "report_blocker":
            return "<output>" + json.dumps(
                {
                    "blocker_type": terminal.get("blocker_type", "out_of_scope_repair"),
                    "required_artifacts": list(terminal.get("required_artifacts", []) or []),
                    "current_allowed_artifacts": list(terminal.get("current_allowed_artifacts", []) or []),
                    "reason": str(terminal.get("reason", "") or ""),
                    **({"suggested_owner_scope": terminal.get("suggested_owner_scope")} if terminal.get("suggested_owner_scope") else {}),
                },
                ensure_ascii=False,
            ) + "</output>"
        summary = str(terminal.get("summary") or "Completed assigned work.").strip()
        changed = [str(value) for value in terminal.get("changed_files", []) or [] if str(value)]
        evidence = [str(value) for value in terminal.get("evidence", []) or [] if str(value)]
        risks = [str(value) for value in terminal.get("risks", []) or [] if str(value)]
        lines = [summary]
        if changed:
            lines.append("Changed files: " + ", ".join(changed))
        if evidence:
            lines.append("Evidence: " + "; ".join(evidence))
        if risks:
            lines.append("Risks: " + "; ".join(risks))
        return "<output>" + "\n".join(lines) + "</output>"

    def _auto_submit_allowed_artifacts(self) -> Dict[str, Any]:
        """Close a worker loop when files exist but the model forgets submit_result."""
        workspace = os.path.abspath(str(getattr(self, "workspace_dir", ".") or "."))
        changed: List[str] = []
        missing: List[str] = []
        compile_errors: List[str] = []
        for artifact in list(getattr(self, "allowed_artifacts", []) or []):
            normalized = os.path.normpath(str(artifact or "")).replace("\\", "/").strip("/")
            if not normalized or normalized.startswith("../"):
                continue
            full_path = os.path.abspath(os.path.join(workspace, normalized))
            if full_path != workspace and not full_path.startswith(workspace + os.sep):
                continue
            if os.path.isfile(full_path):
                if normalized.endswith(".py"):
                    try:
                        py_compile.compile(full_path, doraise=True)
                    except py_compile.PyCompileError as exc:
                        compile_errors.append(f"{normalized}: {exc}")
                        continue
                changed.append(normalized)
            else:
                missing.append(normalized)
        if missing or compile_errors:
            return {}
        if not changed:
            return {}
        return {
            "tool_name": "submit_result",
            "summary": "Runtime auto-submit: owner artifacts exist after tool iteration budget.",
            "changed_files": changed,
            "evidence": [
                "auto_submit_on_iteration_or_timeout_budget",
                f"owner_artifacts_present:{len(changed)}",
                "owner_artifacts_compile:pass",
            ],
            "risks": [
                "Model did not call submit_result before the tool/timeout budget; SliceJudge and promotion still validate the files."
            ],
        }

    def _build_patch_guard(self):
        try:
            from ContractCoding.runtime.repair import PatchGuard

            diagnostics = str(getattr(self, "repair_diagnostics_text", "") or "")
            return PatchGuard(
                self.workspace_dir,
                allowed_artifacts=list(getattr(self, "allowed_artifacts", []) or []),
                diagnostic_text=diagnostics,
                timeout=45,
                validate_imports=bool(diagnostics.strip()),
            )
        except Exception:
            return None

    @classmethod
    def _strict_tool_schema(cls, schema: Dict[str, Any]) -> Dict[str, Any]:
        strict_schema = copy.deepcopy(schema)
        strict_schema.pop("implementation", None)
        function = strict_schema.get("function")
        if isinstance(function, dict):
            function.pop("implementation", None)
            function["strict"] = True
            parameters = function.get("parameters")
            if isinstance(parameters, dict):
                cls._close_json_schema(parameters)
        return strict_schema

    @classmethod
    def _close_json_schema(cls, schema: Dict[str, Any]) -> None:
        schema_type = schema.get("type")
        if schema_type == "object" or "properties" in schema:
            schema.setdefault("type", "object")
            schema["additionalProperties"] = False
            for child in dict(schema.get("properties", {}) or {}).values():
                if isinstance(child, dict):
                    cls._close_json_schema(child)
        if schema_type == "array" and isinstance(schema.get("items"), dict):
            cls._close_json_schema(schema["items"])
        for union_key in ("anyOf", "oneOf", "allOf"):
            for child in schema.get(union_key, []) or []:
                if isinstance(child, dict):
                    cls._close_json_schema(child)

    def _retry_chat(self, **kwargs):
        retry = 0
        adapted_kwargs = dict(kwargs)
        max_retries = max(1, int(adapted_kwargs.pop("max_retries", 3) or 3))
        retry_sleep_seconds = max(0.0, float(adapted_kwargs.pop("retry_sleep_seconds", 10) or 0))
        attempts: List[Dict[str, Any]] = []
        self._last_attempts = []
        while True:
            retry += 1
            started = time.perf_counter()
            try:
                response = self._create_chat_completion_with_deadline(adapted_kwargs)
                attempts.append(
                    {
                        "attempt": retry,
                        "timeout": adapted_kwargs.get("timeout"),
                        "elapsed_seconds": round(time.perf_counter() - started, 4),
                        "returncode": 0,
                        "content_preview": self._response_content_preview(response),
                    }
                )
                self._last_attempts = attempts
                return response
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": retry,
                        "timeout": adapted_kwargs.get("timeout"),
                        "elapsed_seconds": round(time.perf_counter() - started, 4),
                        "returncode": "timeout" if is_timeout_error(exc) else "error",
                        "error_type": exc.__class__.__name__,
                        "error_preview": str(exc)[:300],
                    }
                )
                self._last_attempts = attempts
                adapted = self._adapt_chat_params_after_error(adapted_kwargs, exc)
                if adapted and retry < max_retries:
                    logger.warning("Retrying LLM call with adapted chat parameters after error: %s", exc)
                    adapted_kwargs = adapted
                    continue
                sleep_s = 2 if is_timeout_error(exc) else (30 if is_token_limit_error(exc) else 10)
                logger.error("LLM call error: %s", exc)
                if retry >= max_retries:
                    raise
                if retry_sleep_seconds:
                    time.sleep(min(sleep_s, retry_sleep_seconds))

    def _create_chat_completion_with_deadline(self, kwargs: Dict[str, Any]):
        timeout = max(1.0, float(kwargs.get("timeout") or self.request_timeout or 120))
        hard_timeout = timeout + min(5.0, max(0.5, timeout * 0.1))
        result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

        def run_call() -> None:
            try:
                result_queue.put((True, self.client.chat.completions.create(**kwargs)))
            except Exception as exc:
                result_queue.put((False, exc))

        thread = threading.Thread(target=run_call, daemon=True)
        thread.start()
        thread.join(hard_timeout)
        if thread.is_alive():
            raise TimeoutError(f"OpenAI chat completion hard timeout after {hard_timeout:.1f} seconds")
        try:
            ok, value = result_queue.get_nowait()
        except Empty as exc:
            raise TimeoutError("OpenAI chat completion ended without returning a response") from exc
        if ok:
            return value
        raise value

    def _remaining_tool_timeout(self, started_at: float, *, elapsed: float | None = None) -> int:
        timeout = self.tool_timeout
        if self.tool_loop_timeout:
            if elapsed is None:
                elapsed = time.perf_counter() - started_at
            remaining = max(1, int(self.tool_loop_timeout - elapsed))
            timeout = min(timeout, remaining)
        return max(1, int(timeout))

    def _adapt_chat_params_after_error(self, kwargs: Dict[str, Any], exc: Exception) -> Dict[str, Any] | None:
        message = str(exc).lower()
        adapted = dict(kwargs)
        changed = False
        if "max_tokens" in adapted and (
            "max_tokens" in message
            and ("unsupported" in message or "not supported" in message or "unrecognized" in message)
        ):
            adapted["max_completion_tokens"] = adapted.pop("max_tokens")
            changed = True
        elif "max_completion_tokens" in adapted and (
            "max_completion_tokens" in message
            and ("unsupported" in message or "not supported" in message or "unrecognized" in message)
        ):
            adapted["max_tokens"] = adapted.pop("max_completion_tokens")
            changed = True
        if "temperature" in adapted and "temperature" in message and (
            "unsupported" in message or "not supported" in message or "does not support" in message
        ):
            adapted.pop("temperature", None)
            changed = True
        return adapted if changed else None

    @staticmethod
    def _response_content_preview(response: Any) -> str:
        try:
            message = response.choices[0].message
        except Exception:
            return ""
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            return content[:200]
        tool_calls = list(getattr(message, "tool_calls", []) or [])
        if tool_calls:
            names = [str(getattr(getattr(call, "function", None), "name", "tool")) for call in tool_calls]
            return "[tool_calls: " + ", ".join(names[:8]) + "]"
        return ""
