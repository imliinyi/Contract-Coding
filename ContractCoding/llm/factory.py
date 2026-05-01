"""OpenAI-first LLM backend factory helpers."""

from __future__ import annotations

from ContractCoding.llm.base import LLMBackend
from ContractCoding.llm.openai_backend import OpenAIBackend


def build_backend(config) -> LLMBackend:
    backend = str(getattr(config, "LLM_BACKEND", "openai") or "openai").strip().lower()
    if backend != "openai":
        raise ValueError(f"Unsupported LLM backend: {backend}. ContractCoding is OpenAI-only.")
    return OpenAIBackend(
        api_key=getattr(config, "OPENAI_API_KEY", ""),
        api_base=getattr(config, "OPENAI_API_BASE_URL", ""),
        deployment_name=getattr(config, "OPENAI_DEPLOYMENT_NAME", ""),
        api_version=getattr(config, "OPENAI_API_VERSION", ""),
        max_tokens=int(getattr(config, "OPENAI_API_MAX_TOKENS", 10240)),
        temperature=float(getattr(config, "OPENAI_API_TEMPERATURE", 0.0)),
        tool_approval_mode=getattr(config, "OPENAI_TOOL_APPROVAL_MODE", "auto-edit"),
        request_timeout=int(getattr(config, "OPENAI_API_TIMEOUT", 120)),
        tool_timeout=int(getattr(config, "OPENAI_TOOL_TIMEOUT", 300)),
        image_timeout=int(getattr(config, "OPENAI_IMAGE_TIMEOUT", 180)),
        tool_loop_timeout=int(getattr(config, "OPENAI_TOOL_LOOP_TIMEOUT", 900)),
        max_tool_iterations=int(getattr(config, "OPENAI_MAX_TOOL_ITERATIONS", 10)),
    )
