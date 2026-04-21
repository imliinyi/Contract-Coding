"""Isolated execution planes for module-team work."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional, Set
from uuid import uuid4

from ContractCoding.config import Config
from ContractCoding.utils.log import get_logger


IGNORE_NAMES = {
    ".git",
    ".DS_Store",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
}


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in (value or "workspace"))
    cleaned = cleaned.strip("-").lower()
    return cleaned or "workspace"


@dataclass
class ExecutionPlane:
    mode: str
    module_name: str
    base_workspace_dir: str
    working_dir: str
    root_dir: str
    isolated: bool = False
    repo_root: Optional[str] = None


class ExecutionPlaneManager:
    """Create isolated sandboxes/worktrees for module-team execution."""

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger(config.LOG_PATH)
        self.base_workspace_dir = os.path.abspath(config.WORKSPACE_DIR)
        runtime_root = config.EXECUTION_ROOT.strip() if config.EXECUTION_ROOT else ""
        if runtime_root:
            self.runtime_root = os.path.abspath(runtime_root)
        else:
            self.runtime_root = os.path.abspath(
                os.path.join(os.path.dirname(self.base_workspace_dir), ".contractcoding-execution")
            )
        os.makedirs(self.runtime_root, exist_ok=True)

    def acquire(self, module_name: Optional[str], isolated: bool) -> ExecutionPlane:
        normalized_module = _safe_name(module_name or "root")
        requested_mode = (self.config.EXECUTION_PLANE or "workspace").strip().lower()

        if not isolated or requested_mode == "workspace":
            return ExecutionPlane(
                mode="workspace",
                module_name=normalized_module,
                base_workspace_dir=self.base_workspace_dir,
                working_dir=self.base_workspace_dir,
                root_dir=self.base_workspace_dir,
                isolated=False,
            )

        if requested_mode == "worktree":
            plane = self._create_worktree_plane(normalized_module)
            if plane:
                return plane
            if not self.config.FALLBACK_TO_SANDBOX:
                raise RuntimeError("Unable to create git worktree execution plane and sandbox fallback is disabled.")

        return self._create_sandbox_plane(normalized_module)

    def promote(self, plane: ExecutionPlane, changed_files: Set[str]) -> Set[str]:
        if not plane.isolated:
            return set(changed_files)

        promoted: Set[str] = set()
        for rel_path in sorted(changed_files):
            if not rel_path or rel_path == ".":
                continue
            src = os.path.join(plane.working_dir, rel_path)
            dest = os.path.join(self.base_workspace_dir, rel_path)
            if not os.path.exists(src):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            promoted.add(rel_path)
        return promoted

    def cleanup(self, plane: ExecutionPlane) -> None:
        if not plane.isolated or self.config.KEEP_EXECUTION_PLANES:
            return

        try:
            if plane.mode == "worktree" and plane.repo_root:
                subprocess.run(
                    ["git", "-C", plane.repo_root, "worktree", "remove", "--force", plane.root_dir],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            else:
                shutil.rmtree(plane.root_dir, ignore_errors=True)
        except Exception as exc:
            self.logger.warning("Execution plane cleanup failed for %s: %s", plane.root_dir, exc)

    def _create_sandbox_plane(self, module_name: str) -> ExecutionPlane:
        sandbox_root = os.path.join(self.runtime_root, "sandboxes")
        os.makedirs(sandbox_root, exist_ok=True)

        plane_root = os.path.join(sandbox_root, f"{module_name}-{uuid4().hex[:8]}")
        shutil.copytree(
            self.base_workspace_dir,
            plane_root,
            ignore=shutil.ignore_patterns(*IGNORE_NAMES),
        )
        return ExecutionPlane(
            mode="sandbox",
            module_name=module_name,
            base_workspace_dir=self.base_workspace_dir,
            working_dir=plane_root,
            root_dir=plane_root,
            isolated=True,
        )

    def _create_worktree_plane(self, module_name: str) -> Optional[ExecutionPlane]:
        git_info = self._get_git_workspace_info()
        if git_info is None:
            return None

        repo_root, workspace_rel = git_info
        worktree_root = os.path.join(self.runtime_root, "worktrees", f"{module_name}-{uuid4().hex[:8]}")
        os.makedirs(os.path.dirname(worktree_root), exist_ok=True)

        try:
            subprocess.run(
                ["git", "-C", repo_root, "worktree", "add", "--detach", worktree_root, "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            self.logger.warning("Worktree creation failed for module %s: %s", module_name, exc)
            return None

        working_dir = os.path.join(worktree_root, workspace_rel) if workspace_rel else worktree_root
        return ExecutionPlane(
            mode="worktree",
            module_name=module_name,
            base_workspace_dir=self.base_workspace_dir,
            working_dir=working_dir,
            root_dir=worktree_root,
            isolated=True,
            repo_root=repo_root,
        )

    def _get_git_workspace_info(self) -> Optional[tuple[str, str]]:
        try:
            result = subprocess.run(
                ["git", "-C", self.base_workspace_dir, "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None

        repo_root = result.stdout.strip()
        if not repo_root:
            return None

        workspace_path = Path(self.base_workspace_dir)
        repo_path = Path(repo_root)
        workspace_rel = os.path.relpath(workspace_path, repo_path)
        if workspace_rel == ".":
            workspace_rel = ""
        return repo_root, workspace_rel
