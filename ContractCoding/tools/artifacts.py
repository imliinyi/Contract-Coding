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
        self.artifacts_dir = os.path.join(self.meta_dir, "artifacts")
        self.legacy_meta_path = os.path.join(self.meta_dir, "artifacts.json")
        self._lock = RLock()

    def _ensure_dir(self) -> None:
        os.makedirs(self.artifacts_dir, exist_ok=True)

    def _load_legacy(self) -> Dict[str, Dict[str, int]]:
        os.makedirs(self.meta_dir, exist_ok=True)
        if not os.path.exists(self.legacy_meta_path):
            return {}
        try:
            with open(self.legacy_meta_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _metadata_path(self, rel_path: str) -> str:
        normalized = rel_path.replace("\\", "/").lstrip("./")
        return os.path.join(self.artifacts_dir, f"{normalized}.json")

    def _load_record(self, rel_path: str) -> Dict[str, int]:
        self._ensure_dir()
        meta_path = self._metadata_path(rel_path)
        if not os.path.exists(meta_path):
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_record(self, rel_path: str, data: Dict[str, int]) -> None:
        self._ensure_dir()
        meta_path = self._metadata_path(rel_path)
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)

    def _normalize_path(self, file_path: str) -> str:
        abs_path = os.path.abspath(file_path)
        if abs_path.startswith(self.workspace_dir):
            return os.path.relpath(abs_path, self.workspace_dir)
        return file_path.replace("\\", "/").lstrip("./")

    def get_version(self, file_path: str) -> Optional[int]:
        rel_path = self._normalize_path(file_path)
        with self._lock:
            record = self._load_record(rel_path)
            if "version" in record:
                return record.get("version")
            return self._load_legacy().get(rel_path, {}).get("version")

    def bump_version(self, file_path: str) -> int:
        rel_path = self._normalize_path(file_path)
        with self._lock:
            record = self._load_record(rel_path)
            if "version" in record:
                current_version = int(record.get("version", 0))
            else:
                current_version = int(self._load_legacy().get(rel_path, {}).get("version", 0))
            next_version = current_version + 1
            self._save_record(rel_path, {"version": next_version})
            return next_version
