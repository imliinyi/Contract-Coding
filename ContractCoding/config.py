"""Configuration for ContractCoding."""

import os

from pydantic import BaseModel


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config(BaseModel):
    TERMINATION_POLICY: str = "all"
    SPEC_GATING_ENABLED: bool = False
    WORKSPACE_DIR: str = os.getenv("WORKSPACE_DIR", "workspace")
    MEMORY_WINDOW: int = 5
    MAX_LAYERS: int = 20
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "16"))
    LOG_PATH: str = os.getenv("LOG_PATH", "./agent.log")

    # Model backend. Use "openai" for the original API backend or "codex_cli"
    # to call a local Codex CLI process in read-only mode.
    MODEL_BACKEND: str = os.getenv("MODEL_BACKEND", "openai")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "Your OpenAI API Key")
    OPENAI_API_BASE_URL: str = os.getenv(
        "OPENAI_API_BASE_URL",
        "https://api.openai.com/v1",
    )
    OPENAI_DEPLOYMENT_NAME: str = os.getenv("OPENAI_DEPLOYMENT_NAME", "gpt-4o-2024-11-20")
    OPENAI_API_MAX_TOKENS: int = int(os.getenv("OPENAI_API_MAX_TOKENS", "16384"))
    OPENAI_API_TEMPERATURE: float = float(os.getenv("OPENAI_API_TEMPERATURE", "0.2"))

    # Codex CLI backend settings. The default command is intentionally read-only.
    # Windows users can override CODEX_CLI_COMMAND if their installed Codex CLI
    # uses different non-interactive flags.
    CODEX_CLI_COMMAND: str = os.getenv(
        "CODEX_CLI_COMMAND",
        "codex exec --sandbox read-only --ask-for-approval never -",
    )
    CODEX_CLI_WORKDIR: str = os.getenv("CODEX_CLI_WORKDIR", ".")
    CODEX_CLI_TIMEOUT: int = int(os.getenv("CODEX_CLI_TIMEOUT", "300"))
    CODEX_CLI_MAX_OUTPUT_CHARS: int = int(os.getenv("CODEX_CLI_MAX_OUTPUT_CHARS", "200000"))
    CODEX_CLI_READ_ONLY: bool = _env_bool("CODEX_CLI_READ_ONLY", True)
