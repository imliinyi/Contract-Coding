"""Configuration for ContractCoding.

Only fields required by the registry-based runtime + the OpenAI LLM port.
"""

from __future__ import annotations

import os

from pydantic import BaseModel


class Config(BaseModel):
    # Filesystem
    WORKSPACE_DIR: str = os.getenv("WORKSPACE_DIR", "workspace")
    LOG_PATH: str = os.getenv("LOG_PATH", "./agent.log")

    # OpenAI / Azure OpenAI
    OPENAI_API_KEY: str = os.getenv("API_KEY", os.getenv("OPENAI_API_KEY", ""))
    OPENAI_API_BASE_URL: str = os.getenv(
        "BASE_URL",
        os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
    )
    OPENAI_API_VERSION: str = os.getenv("API_VERSION", os.getenv("OPENAI_API_VERSION", ""))
    OPENAI_DEPLOYMENT_NAME: str = os.getenv(
        "OPENAI_DEPLOYMENT_NAME",
        os.getenv("MODEL_NAME", "gpt-4o-mini"),
    )
    OPENAI_API_MAX_TOKENS: int = int(os.getenv("OPENAI_API_MAX_TOKENS", "8192"))
    OPENAI_API_TEMPERATURE: float = float(os.getenv("OPENAI_API_TEMPERATURE", "0.0"))
    OPENAI_API_TIMEOUT: int = int(os.getenv("OPENAI_API_TIMEOUT", "120"))

    # Coordinator
    MAX_PARALLEL_TEAMS: int = int(os.getenv("MAX_PARALLEL_TEAMS", "4"))
    MAX_PARALLEL_ITEMS_PER_TEAM: int = int(os.getenv("MAX_PARALLEL_ITEMS_PER_TEAM", "1"))
    AUTO_ITEM_REPAIR_MAX: int = int(os.getenv("AUTO_ITEM_REPAIR_MAX", "1"))
    MAX_TICKS: int = int(os.getenv("MAX_TICKS", "200"))
    OFFLINE_LLM: bool = os.getenv("OFFLINE_LLM", "false").lower() == "true"
