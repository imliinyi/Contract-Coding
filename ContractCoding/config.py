"""Configuration for ContractCoding."""

import os

from pydantic import BaseModel


class Config(BaseModel):
    TERMINATION_POLICY: str = "all"
    SPEC_GATING_ENABLED: bool = False
    WORKSPACE_DIR: str = os.getenv("WORKSPACE_DIR", "workspace")
    EXECUTION_PLANE: str = os.getenv("EXECUTION_PLANE", "workspace")
    EXECUTION_ROOT: str = os.getenv("EXECUTION_ROOT", "")
    KEEP_EXECUTION_PLANES: bool = os.getenv("KEEP_EXECUTION_PLANES", "false").lower() == "true"
    FALLBACK_TO_SANDBOX: bool = os.getenv("FALLBACK_TO_SANDBOX", "true").lower() == "true"
    MEMORY_WINDOW: int = 5
    MAX_LAYERS: int = 20
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "16"))
    LOG_PATH: str = os.getenv("LOG_PATH", "./agent.log")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "Your OpenAI API Key")
    OPENAI_API_BASE_URL: str = os.getenv(
        "OPENAI_API_BASE_URL",
        "https://api.openai.com/v1",
    )
    OPENAI_DEPLOYMENT_NAME: str = os.getenv("OPENAI_DEPLOYMENT_NAME", "gpt-4o-2024-11-20")
    OPENAI_API_MAX_TOKENS: int = int(os.getenv("OPENAI_API_MAX_TOKENS", "16384"))
    OPENAI_API_TEMPERATURE: float = float(os.getenv("OPENAI_API_TEMPERATURE", "0.2"))
