"""Thread-local workspace routing for isolated execution planes."""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
from typing import Iterator


_WORKSPACE_LOCAL = threading.local()


def get_current_workspace(default_workspace: str) -> str:
    override = getattr(_WORKSPACE_LOCAL, "workspace_dir", None)
    return os.path.abspath(override or default_workspace)


@contextmanager
def workspace_scope(workspace_dir: str) -> Iterator[None]:
    previous = getattr(_WORKSPACE_LOCAL, "workspace_dir", None)
    _WORKSPACE_LOCAL.workspace_dir = os.path.abspath(workspace_dir)
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_WORKSPACE_LOCAL, "workspace_dir")
            except AttributeError:
                pass
        else:
            _WORKSPACE_LOCAL.workspace_dir = previous
