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
    MEMORY_WINDOW: int = int(os.getenv("MEMORY_WINDOW", "5"))
    CONTEXT_MAX_CHARS: int = int(os.getenv("CONTEXT_MAX_CHARS", "24000"))
    CONTEXT_SKILL_CHARS: int = int(os.getenv("CONTEXT_SKILL_CHARS", "4000"))
    ENABLE_BUILTIN_SKILLS: bool = os.getenv("ENABLE_BUILTIN_SKILLS", "true").lower() == "true"
    SKILL_PATHS: str = os.getenv("SKILL_PATHS", "")
    MAX_LAYERS: int = 20
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "16"))
    MAX_PARALLEL_TEAMS: int = int(os.getenv("MAX_PARALLEL_TEAMS", "4"))
    MAX_PARALLEL_ITEMS_PER_TEAM: int = int(os.getenv("MAX_PARALLEL_ITEMS_PER_TEAM", "4"))
    LLM_PLANNER_ENABLED: bool = os.getenv("LLM_PLANNER_ENABLED", "false").lower() == "true"
    AUTO_REPLAN_MAX: int = int(os.getenv("AUTO_REPLAN_MAX", "2"))
    AUTO_RETRY_MAX_PER_ITEM: int = int(os.getenv("AUTO_RETRY_MAX_PER_ITEM", "2"))
    AUTO_INFRA_RETRY_MAX: int = int(os.getenv("AUTO_INFRA_RETRY_MAX", "2"))
    AUTO_ITEM_REPAIR_MAX: int = int(os.getenv("AUTO_ITEM_REPAIR_MAX", os.getenv("AUTO_RETRY_MAX_PER_ITEM", "2")))
    AUTO_TEST_REPAIR_MAX: int = int(os.getenv("AUTO_TEST_REPAIR_MAX", "4"))
    AUTO_CONTRACT_REPLAN_MAX: int = int(os.getenv("AUTO_CONTRACT_REPLAN_MAX", os.getenv("AUTO_REPLAN_MAX", "2")))
    AUTO_MAX_STEWARD_LOOPS: int = int(os.getenv("AUTO_MAX_STEWARD_LOOPS", "12"))
    CONTRACTCODING_TEST_DISCOVERY: str = os.getenv("CONTRACTCODING_TEST_DISCOVERY", "auto")
    CONTRACTCODING_TEAM_GATE_MODE: str = os.getenv("CONTRACTCODING_TEAM_GATE_MODE", "smoke")
    CONTRACTCODING_PARTIAL_PROMOTION: bool = os.getenv("CONTRACTCODING_PARTIAL_PROMOTION", "true").lower() == "true"
    PROMOTION_PATCH_SUMMARY: bool = os.getenv("PROMOTION_PATCH_SUMMARY", "true").lower() == "true"
    LOG_PATH: str = os.getenv("LOG_PATH", "./agent.log")

    OPENAI_API_KEY: str = os.getenv("API_KEY", os.getenv("OPENAI_API_KEY", "Your OpenAI API Key"))
    OPENAI_API_BASE_URL: str = os.getenv(
        "BASE_URL",
        os.getenv(
            "OPENAI_API_BASE_URL",
            "https://api.openai.com/v1",
        ),
    )
    OPENAI_API_VERSION: str = os.getenv("API_VERSION", os.getenv("OPENAI_API_VERSION", ""))
    OPENAI_DEPLOYMENT_NAME: str = os.getenv(
        "OPENAI_DEPLOYMENT_NAME",
        os.getenv("MODEL_NAME", "gpt-5.4-2026-03-05"),
    )
    OPENAI_API_MAX_TOKENS: int = int(os.getenv("OPENAI_API_MAX_TOKENS", "16384"))
    OPENAI_API_TEMPERATURE: float = float(os.getenv("OPENAI_API_TEMPERATURE", "0.2"))
    OPENAI_API_TIMEOUT: int = int(os.getenv("OPENAI_API_TIMEOUT", "120"))
    OPENAI_TOOL_TIMEOUT: int = int(os.getenv("OPENAI_TOOL_TIMEOUT", "120"))
    OPENAI_IMAGE_TIMEOUT: int = int(os.getenv("OPENAI_IMAGE_TIMEOUT", "180"))
    OPENAI_TOOL_LOOP_TIMEOUT: int = int(os.getenv("OPENAI_TOOL_LOOP_TIMEOUT", "300"))
    OPENAI_MAX_TOOL_ITERATIONS: int = int(os.getenv("OPENAI_MAX_TOOL_ITERATIONS", "10"))
    OPENAI_TOOL_APPROVAL_MODE: str = os.getenv("OPENAI_TOOL_APPROVAL_MODE", "auto-edit")

    LLM_BACKEND: str = os.getenv("LLM_BACKEND", "openai")
    LLM_FALLBACK_ORDER: str = os.getenv("LLM_FALLBACK_ORDER", "openai")

    RUN_STORE_PATH: str = os.getenv("RUN_STORE_PATH", "")
