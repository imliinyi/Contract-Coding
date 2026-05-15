"""OpenAI / Azure OpenAI implementation of LLMPort.

Builds system + user messages and calls `chat.completions.create`. Azure is
selected when `OPENAI_API_VERSION` is non-empty.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..config import Config
from ..worker.protocol import LLMRequest, LLMResult


class OpenAILLMPort:
    """Concrete LLMPort that talks to OpenAI (or Azure OpenAI).

    This is the only piece of the runtime that depends on the `openai`
    package. Passes consume it through the `LLMPort` protocol only.
    """

    def __init__(self, config: Config, *, model: Optional[str] = None) -> None:
        self.config = config
        self.model = model or config.OPENAI_DEPLOYMENT_NAME
        self._client = self._build_client(config)

    # ------------------------------------------------------------------ client

    @staticmethod
    def _build_client(config: Config) -> Any:
        api_version = config.OPENAI_API_VERSION.strip()
        if api_version:
            from openai import AzureOpenAI  # type: ignore

            return AzureOpenAI(
                api_key=config.OPENAI_API_KEY,
                api_version=api_version,
                azure_endpoint=config.OPENAI_API_BASE_URL,
                timeout=config.OPENAI_API_TIMEOUT,
            )
        from openai import OpenAI  # type: ignore

        return OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_API_BASE_URL or None,
            timeout=config.OPENAI_API_TIMEOUT,
        )

    # ----------------------------------------------------------------- complete

    def complete(self, request: LLMRequest) -> LLMResult:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})

        max_tokens = int(request.max_tokens or self.config.OPENAI_API_MAX_TOKENS)
        temperature = float(request.temperature or self.config.OPENAI_API_TEMPERATURE)

        start = time.time()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = (time.time() - start) * 1000.0

        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        finish_reason = getattr(choice, "finish_reason", "stop") or "stop"

        raw: Dict[str, Any] = {
            "model": getattr(response, "model", self.model),
            "id": getattr(response, "id", ""),
            "finish_reason": finish_reason,
        }
        usage = getattr(response, "usage", None)
        if usage is not None:
            raw["usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            }

        return LLMResult(
            text=text,
            raw=raw,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )

