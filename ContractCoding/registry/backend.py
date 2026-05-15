"""Filesystem-backed, path-scoped Registry storage.

Layout under `<root>/`:

    plan.json
    contract/project.json
    contract/teams/<team_id>.json
    contract/operations.jsonl
    contract/obligations.jsonl
    contract/schedule.jsonl
    contract/evidence.jsonl
    events.log                              (jsonl, append-only)
    capsules/<team_id>/<capability>.json
    workspace/<team_id>/                    (team-owned files; not managed here)
    ledgers/<team_id>/working_paper.json
    ledgers/<team_id>/task_ledger.json
    ledgers/<team_id>/progress_ledger.jsonl
    ledgers/<team_id>/failure_ledger.jsonl
    ledgers/<team_id>/reviewer_memory.json
    escalations/<id>.json

The backend is intentionally policy-free. ACL + ergonomic helpers live one
layer up in `RegistryTool`. This split lets us mock the policy layer in
tests without losing durability guarantees.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import threading
from typing import Any, Dict, Iterable, List, Optional

from ..core.events import Event, EventLog


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryPath:
    """Logical, slash-delimited registry path.

    Always normalised to forward slashes and rooted at `/`. Resolves to a
    real filesystem path via `RegistryBackend.resolve()`.
    """

    raw: str

    def normalised(self) -> str:
        p = self.raw.strip()
        if not p.startswith("/"):
            p = "/" + p
        # collapse `..` and `.`
        parts: List[str] = []
        for segment in p.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(segment)
        return "/" + "/".join(parts)

    def parts(self) -> List[str]:
        return [p for p in self.normalised().split("/") if p]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.normalised()


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class RegistryBackend:
    """Path-scoped, thread-safe filesystem registry.

    Operations are intentionally narrow:
      - read_json / write_json (atomic)
      - append_jsonl / read_jsonl
      - list_dir / exists / remove
      - emit_event / read_events (forward to EventLog)

    Concurrency strategy:
      * one process-global RLock guards rename-into-place writes;
      * append-only files use OS-level append (atomic for small lines on POSIX);
      * EventLog has its own internal lock.
    """

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.RLock()
        self.event_log = EventLog(os.path.join(self.root, "events.log"))

    # ----- path resolution -----

    def resolve(self, path: RegistryPath) -> str:
        """Map a logical RegistryPath to a real filesystem path under root."""
        rel = path.normalised().lstrip("/")
        full = os.path.abspath(os.path.join(self.root, rel))
        # security: must remain inside root
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ValueError(f"path escapes registry root: {path.raw!r}")
        return full

    # ----- json read/write -----

    def read_json(self, path: RegistryPath) -> Optional[Dict[str, Any]]:
        full = self.resolve(path)
        if not os.path.exists(full):
            return None
        with self._lock:
            with open(full, "r", encoding="utf-8") as handle:
                return json.load(handle)

    def write_json(self, path: RegistryPath, payload: Dict[str, Any]) -> None:
        full = self.resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".tmp"
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(data)
            os.replace(tmp, full)

    # ----- jsonl append/read -----

    def append_jsonl(self, path: RegistryPath, record: Dict[str, Any]) -> None:
        full = self.resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with open(full, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")

    def read_jsonl(
        self,
        path: RegistryPath,
        *,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        full = self.resolve(path)
        if not os.path.exists(full):
            return []
        out: List[Dict[str, Any]] = []
        with self._lock:
            with open(full, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if limit and len(out) >= limit:
                        break
        return out

    # ----- file plumbing -----

    def exists(self, path: RegistryPath) -> bool:
        return os.path.exists(self.resolve(path))

    def list_dir(self, path: RegistryPath) -> List[str]:
        full = self.resolve(path)
        if not os.path.isdir(full):
            return []
        return sorted(os.listdir(full))

    def remove(self, path: RegistryPath) -> None:
        full = self.resolve(path)
        with self._lock:
            if os.path.isfile(full):
                os.remove(full)

    def write_text(self, path: RegistryPath, content: str) -> None:
        """For non-JSON artifacts (MANIFEST.md, stub files, etc.)."""
        full = self.resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp, full)

    def write_text_if_hash(
        self,
        path: RegistryPath,
        content: str,
        *,
        expected_sha256: str,
    ) -> tuple[bool, str]:
        """Compare-and-swap text write.

        Returns `(written, observed_sha256)`. The empty string represents a
        missing file. This is intentionally small but gives the worker a
        Git-like lost-update guard for parallel writes.
        """
        full = self.resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".tmp"
        with self._lock:
            if os.path.exists(full):
                with open(full, "r", encoding="utf-8") as handle:
                    observed_text = handle.read()
                observed_sha = hashlib.sha256(observed_text.encode("utf-8")).hexdigest()
            else:
                observed_sha = ""
            if observed_sha != expected_sha256:
                return (False, observed_sha)
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp, full)
            return (True, hashlib.sha256(content.encode("utf-8")).hexdigest())

    def read_text(self, path: RegistryPath) -> Optional[str]:
        full = self.resolve(path)
        if not os.path.exists(full):
            return None
        with open(full, "r", encoding="utf-8") as handle:
            return handle.read()

    # ----- events -----

    def emit_event(self, event: Event) -> Event:
        return self.event_log.append(event)

    def read_events(self, **kwargs: Any) -> List[Event]:
        return self.event_log.read(**kwargs)
