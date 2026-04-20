"""Artifact metadata helpers used by workspace file tools and audits."""

from __future__ import annotations

import json
import os
from threading import RLock
from typing import Dict, Optional


class ArtifactMetadataStore:
    """Persist per-file metadata outside the source files themselves."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.meta_dir = os.path.join(self.workspace_dir, ".contractcoding")
        self.meta_path = os.path.join(self.meta_dir, "artifacts.json")
        self._lock = RLock()

    def _ensure_dir(self) -> None:
        os.makedirs(self.meta_dir, exist_ok=True)

    def _load(self) -> Dict[str, Dict[str, int]]:
        self._ensure_dir()
        if not os.path.exists(self.meta_path):
            return {}
        try:
            with open(self.meta_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: Dict[str, Dict[str, int]]) -> None:
        self._ensure_dir()
        with open(self.meta_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)

    def _normalize_path(self, file_path: str) -> str:
        abs_path = os.path.abspath(file_path)
        if abs_path.startswith(self.workspace_dir):
            return os.path.relpath(abs_path, self.workspace_dir)
        return file_path.replace("\\", "/").lstrip("./")

    def get_version(self, file_path: str) -> Optional[int]:
        rel_path = self._normalize_path(file_path)
        with self._lock:
            return self._load().get(rel_path, {}).get("version")

    def bump_version(self, file_path: str) -> int:
        rel_path = self._normalize_path(file_path)
        with self._lock:
            data = self._load()
            current_version = int(data.get(rel_path, {}).get("version", 0))
            next_version = current_version + 1
            data[rel_path] = {"version": next_version}
            self._save(data)
            return next_version
