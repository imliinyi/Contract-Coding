"""Isolated execution planes for module-team work."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import RLock
from typing import Dict, Optional, Set
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
    baseline_hashes: Dict[str, Optional[str]] | None = None
    baseline_dir: Optional[str] = None


class ExecutionPlanePromotionError(RuntimeError):
    """Raised when an isolated plane cannot be promoted safely."""


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
        self._promotion_lock = RLock()

    def acquire(self, module_name: Optional[str], isolated: bool) -> ExecutionPlane:
        normalized_module = _safe_name(module_name or "root")
        requested_mode = (self.config.EXECUTION_PLANE or "workspace").strip().lower()
        baseline_hashes = self._capture_file_hashes(self.base_workspace_dir)

        if not isolated or requested_mode == "workspace":
            return ExecutionPlane(
                mode="workspace",
                module_name=normalized_module,
                base_workspace_dir=self.base_workspace_dir,
                working_dir=self.base_workspace_dir,
                root_dir=self.base_workspace_dir,
                isolated=False,
                baseline_hashes=baseline_hashes,
                baseline_dir=None,
            )

        baseline_dir = self._create_baseline_snapshot(normalized_module)

        if requested_mode == "worktree":
            plane = self._create_worktree_plane(normalized_module, baseline_hashes, baseline_dir)
            if plane:
                return plane
            if not self.config.FALLBACK_TO_SANDBOX:
                shutil.rmtree(baseline_dir, ignore_errors=True)
                raise RuntimeError("Unable to create git worktree execution plane and sandbox fallback is disabled.")

        return self._create_sandbox_plane(normalized_module, baseline_hashes, baseline_dir)

    def promote(self, plane: ExecutionPlane, changed_files: Set[str]) -> Set[str]:
        if not plane.isolated:
            return set(changed_files)

        with self._promotion_lock:
            promoted: Set[str] = set()
            for rel_path in sorted(changed_files):
                if not rel_path or rel_path == ".":
                    continue
                src = os.path.join(plane.working_dir, rel_path)
                dest = os.path.join(self.base_workspace_dir, rel_path)
                baseline = os.path.join(plane.baseline_dir, rel_path) if plane.baseline_dir else None
                os.makedirs(os.path.dirname(dest), exist_ok=True)

                baseline_hash = (plane.baseline_hashes or {}).get(rel_path)
                current_hash = self._hash_file(dest)
                if current_hash == baseline_hash:
                    if os.path.exists(src):
                        shutil.copy2(src, dest)
                else:
                    self._merge_changed_file(src=src, baseline=baseline, dest=dest, rel_path=rel_path)
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
            elif plane.isolated:
                shutil.rmtree(plane.root_dir, ignore_errors=True)
            if plane.baseline_dir and os.path.exists(plane.baseline_dir):
                shutil.rmtree(plane.baseline_dir, ignore_errors=True)
        except Exception as exc:
            self.logger.warning("Execution plane cleanup failed for %s: %s", plane.root_dir, exc)

    def _create_sandbox_plane(
        self,
        module_name: str,
        baseline_hashes: Dict[str, Optional[str]],
        baseline_dir: str,
    ) -> ExecutionPlane:
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
            baseline_hashes=dict(baseline_hashes),
            baseline_dir=baseline_dir,
        )

    def _create_worktree_plane(
        self,
        module_name: str,
        baseline_hashes: Dict[str, Optional[str]],
        baseline_dir: str,
    ) -> Optional[ExecutionPlane]:
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
        os.makedirs(working_dir, exist_ok=True)
        self._sync_workspace_snapshot(self.base_workspace_dir, working_dir, baseline_hashes)
        return ExecutionPlane(
            mode="worktree",
            module_name=module_name,
            base_workspace_dir=self.base_workspace_dir,
            working_dir=working_dir,
            root_dir=worktree_root,
            isolated=True,
            repo_root=repo_root,
            baseline_hashes=dict(baseline_hashes),
            baseline_dir=baseline_dir,
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

        workspace_path = Path(os.path.realpath(self.base_workspace_dir))
        repo_path = Path(os.path.realpath(repo_root))
        workspace_rel = os.path.relpath(workspace_path, repo_path)
        if workspace_rel == ".":
            workspace_rel = ""
        return repo_root, workspace_rel

    def _capture_file_hashes(self, root_dir: str) -> Dict[str, Optional[str]]:
        snapshot: Dict[str, Optional[str]] = {}
        for current_root, dirs, files in os.walk(root_dir):
            dirs[:] = [name for name in dirs if name not in IGNORE_NAMES]
            for file_name in files:
                if file_name in IGNORE_NAMES:
                    continue
                abs_path = os.path.join(current_root, file_name)
                rel_path = os.path.relpath(abs_path, root_dir)
                snapshot[rel_path] = self._hash_file(abs_path)
        return snapshot

    def _hash_file(self, file_path: str) -> Optional[str]:
        if not os.path.exists(file_path):
            return None
        digest = hashlib.sha256()
        with open(file_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _sync_workspace_snapshot(
        self,
        source_dir: str,
        target_dir: str,
        baseline_hashes: Dict[str, Optional[str]],
    ) -> None:
        target_hashes = self._capture_file_hashes(target_dir)

        for rel_path in sorted(target_hashes):
            if rel_path not in baseline_hashes:
                try:
                    os.remove(os.path.join(target_dir, rel_path))
                except FileNotFoundError:
                    pass

        for rel_path in sorted(baseline_hashes):
            src = os.path.join(source_dir, rel_path)
            dest = os.path.join(target_dir, rel_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)

    def _create_baseline_snapshot(self, module_name: str) -> str:
        baseline_root = os.path.join(self.runtime_root, "baselines")
        os.makedirs(baseline_root, exist_ok=True)
        snapshot_dir = os.path.join(baseline_root, f"{module_name}-{uuid4().hex[:8]}")
        shutil.copytree(
            self.base_workspace_dir,
            snapshot_dir,
            ignore=shutil.ignore_patterns(*IGNORE_NAMES),
        )
        return snapshot_dir

    def _merge_changed_file(self, src: str, baseline: Optional[str], dest: str, rel_path: str) -> None:
        if not os.path.exists(src):
            raise ExecutionPlanePromotionError(
                f"Execution plane promotion cannot merge deleted or missing file '{rel_path}' safely."
            )

        if not baseline or not os.path.exists(baseline):
            raise ExecutionPlanePromotionError(
                f"Execution plane promotion cannot auto-merge new file '{rel_path}' because the base workspace changed."
            )

        if self._looks_binary(src) or self._looks_binary(dest) or self._looks_binary(baseline):
            raise ExecutionPlanePromotionError(
                f"Execution plane promotion cannot auto-merge binary or non-text file '{rel_path}'."
            )

        merged_content = self._run_git_merge_file(current=dest, base=baseline, incoming=src, rel_path=rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as handle:
            handle.write(merged_content)

    def _looks_binary(self, file_path: str) -> bool:
        if not os.path.exists(file_path):
            return False
        try:
            with open(file_path, "rb") as handle:
                chunk = handle.read(4096)
            if b"\x00" in chunk:
                return True
            chunk.decode("utf-8")
            return False
        except UnicodeDecodeError:
            return True

    def _run_git_merge_file(self, current: str, base: str, incoming: str, rel_path: str) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            current_tmp = os.path.join(tmpdir, "current")
            base_tmp = os.path.join(tmpdir, "base")
            incoming_tmp = os.path.join(tmpdir, "incoming")
            shutil.copy2(current, current_tmp)
            shutil.copy2(base, base_tmp)
            shutil.copy2(incoming, incoming_tmp)

            result = subprocess.run(
                ["git", "merge-file", "-p", current_tmp, base_tmp, incoming_tmp],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                return result.stdout
            if result.returncode == 1:
                raise ExecutionPlanePromotionError(
                    f"Execution plane promotion hit merge conflicts for '{rel_path}'."
                )
            raise ExecutionPlanePromotionError(
                f"Execution plane promotion could not merge '{rel_path}': {result.stderr.strip() or result.stdout.strip()}"
            )
