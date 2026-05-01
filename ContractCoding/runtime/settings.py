"""Minimal hot-reloadable runtime settings for ContractCoding Runtime V4."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Dict

from ContractCoding.config import Config


SETTINGS_FILE = os.path.join(".contractcoding", "settings.json")


@dataclass(frozen=True)
class RuntimeSettings:
    llm_backend: str
    llm_fallback_order: str
    max_parallel_teams: int
    max_parallel_items_per_team: int
    llm_planner_enabled: bool
    context_max_chars: int
    context_skill_chars: int
    skill_paths: str
    hooks_enabled: bool
    execution_plane: str

    def scheduler_overrides(self) -> Dict[str, Any]:
        return {
            "max_parallel_teams": self.max_parallel_teams,
            "max_parallel_items_per_team": self.max_parallel_items_per_team,
            "default_execution_plane": self.execution_plane,
        }


class SettingsManager:
    """Read defaults < settings file < env/current config for the next runtime loop."""

    def __init__(self, config: Config):
        self.config = config
        self.workspace_dir = os.path.abspath(config.WORKSPACE_DIR)
        self.path = os.path.join(self.workspace_dir, SETTINGS_FILE)

    def snapshot(self) -> RuntimeSettings:
        data = self._file_settings()
        return RuntimeSettings(
            llm_backend=str(os.getenv("LLM_BACKEND", data.get("backend", self.config.LLM_BACKEND))),
            llm_fallback_order=str(
                os.getenv("LLM_FALLBACK_ORDER", data.get("llm_fallback_order", self.config.LLM_FALLBACK_ORDER))
            ),
            max_parallel_teams=int(
                os.getenv("MAX_PARALLEL_TEAMS", data.get("max_parallel_teams", self.config.MAX_PARALLEL_TEAMS))
            ),
            max_parallel_items_per_team=int(
                os.getenv(
                    "MAX_PARALLEL_ITEMS_PER_TEAM",
                    data.get("max_parallel_items_per_team", self.config.MAX_PARALLEL_ITEMS_PER_TEAM),
                )
            ),
            llm_planner_enabled=self._bool(
                os.getenv("LLM_PLANNER_ENABLED", data.get("llm_planner_enabled", self.config.LLM_PLANNER_ENABLED))
            ),
            context_max_chars=int(os.getenv("CONTEXT_MAX_CHARS", data.get("context_max_chars", self.config.CONTEXT_MAX_CHARS))),
            context_skill_chars=int(
                os.getenv("CONTEXT_SKILL_CHARS", data.get("context_skill_chars", self.config.CONTEXT_SKILL_CHARS))
            ),
            skill_paths=str(os.getenv("SKILL_PATHS", data.get("skill_paths", self.config.SKILL_PATHS))),
            hooks_enabled=self._bool(os.getenv("HOOKS_ENABLED", data.get("hooks_enabled", True))),
            execution_plane=str(os.getenv("EXECUTION_PLANE", data.get("execution_plane", self.config.EXECUTION_PLANE))),
        )

    def apply_to_config(self, settings: RuntimeSettings) -> None:
        self.config.LLM_BACKEND = settings.llm_backend
        self.config.LLM_FALLBACK_ORDER = settings.llm_fallback_order
        self.config.MAX_PARALLEL_TEAMS = settings.max_parallel_teams
        self.config.MAX_PARALLEL_ITEMS_PER_TEAM = settings.max_parallel_items_per_team
        self.config.LLM_PLANNER_ENABLED = settings.llm_planner_enabled
        self.config.CONTEXT_MAX_CHARS = settings.context_max_chars
        self.config.CONTEXT_SKILL_CHARS = settings.context_skill_chars
        self.config.SKILL_PATHS = settings.skill_paths
        self.config.EXECUTION_PLANE = settings.execution_plane

    def _file_settings(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}
