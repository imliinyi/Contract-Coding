"""Promotion metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import subprocess
from typing import Dict, Iterable, List


@dataclass
class PromotionPatchSummary:
    run_id: str
    team_id: str
    scope_id: str
    changed_files: List[str]
    promoted_files: List[str] = field(default_factory=list)
    owned_files: List[str] = field(default_factory=list)
    unowned_files: List[str] = field(default_factory=list)
    patch_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    conflict_reason: str = ""

    def to_record(self) -> dict:
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "scope_id": self.scope_id,
            "changed_files": list(self.changed_files),
            "promoted_files": list(self.promoted_files),
            "owned_files": list(self.owned_files),
            "unowned_files": list(self.unowned_files),
            "patch_stats": dict(self.patch_stats),
            "conflict_reason": self.conflict_reason,
        }


class PromotionMetadataWriter:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def build(
        self,
        *,
        run_id: str,
        team_id: str,
        scope_id: str,
        working_dir: str,
        base_dir: str,
        changed_files: Iterable[str],
        owned_artifacts: Iterable[str],
        promoted_files: Iterable[str] = (),
        conflict_reason: str = "",
    ) -> PromotionPatchSummary:
        changed = sorted({self._normalize(path) for path in changed_files if path})
        owned = {self._normalize(path) for path in owned_artifacts if path}
        promoted = sorted({self._normalize(path) for path in promoted_files if path})
        patch_stats = {
            path: self._file_patch_stats(os.path.join(base_dir, path), os.path.join(working_dir, path))
            for path in changed
        }
        return PromotionPatchSummary(
            run_id=run_id,
            team_id=team_id,
            scope_id=scope_id,
            changed_files=changed,
            promoted_files=promoted,
            owned_files=[path for path in changed if path in owned],
            unowned_files=[path for path in changed if path not in owned],
            patch_stats=patch_stats,
            conflict_reason=conflict_reason,
        )

    def write(self, summary: PromotionPatchSummary) -> str:
        rel_path = os.path.join(".contractcoding", "promotions", summary.run_id, f"{self._safe_name(summary.scope_id)}.json")
        path = os.path.join(self.workspace_dir, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(summary.to_record(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return rel_path.replace("\\", "/")

    def _file_patch_stats(self, before: str, after: str) -> Dict[str, int]:
        if not os.path.exists(after):
            return {"added": 0, "deleted": 0, "sha256": ""}
        digest = self._sha256(after)
        if not os.path.exists(before):
            return {"added": self._line_count(after), "deleted": 0, "sha256": digest}
        try:
            result = subprocess.run(
                ["git", "diff", "--no-index", "--numstat", before, after],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return {"added": 0, "deleted": 0, "sha256": digest}
        first = (result.stdout or "").splitlines()[0:1]
        if not first:
            return {"added": 0, "deleted": 0, "sha256": digest}
        parts = first[0].split()
        try:
            added = int(parts[0]) if parts[0].isdigit() else 0
            deleted = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        except (ValueError, IndexError):
            added = deleted = 0
        return {"added": added, "deleted": deleted, "sha256": digest}

    @staticmethod
    def _normalize(path: str) -> str:
        normalized = os.path.normpath(str(path or "").replace("\\", "/")).replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    @staticmethod
    def _safe_name(value: str) -> str:
        return "".join(ch if ch.isalnum() else "-" for ch in (value or "scope")).strip("-").lower() or "scope"

    @staticmethod
    def _line_count(path: str) -> int:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return sum(1 for _ in handle)
        except UnicodeDecodeError:
            return 0

    @staticmethod
    def _sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
