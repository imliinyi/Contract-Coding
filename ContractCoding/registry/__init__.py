"""Contract Registry — durable store for contract state, capsules, and ledgers.

The registry is the pull-based shared artefact store. Teams read typed
contract state and private ledgers from it; cross-team coordination flows
through reduced contract operations, not prompt messages.

Surface split:

  backend.py
      `RegistryBackend` — filesystem-backed, path-scoped read/write with an
      append-only event tail. No policy; just durable I/O + indexing.

  acl.py
      `RegistryACL` — policy layer mapping (actor, operation, path) → allow.
      Matches Part 2 §3.2 of the design doc (path-scoped writers).

  tool.py
      `RegistryTool` — the producer/consumer API actually exposed to agents.
      Everything agents can do with the registry flows through this class so
      that we can audit, rate-limit, and validate every call.
"""

from __future__ import annotations

from .backend import RegistryBackend, RegistryPath
from .acl import (
    ACLDecision,
    Actor,
    Op,
    RegistryACL,
    RegistryAccessError,
    default_policy,
)
from .tool import RegistryTool

__all__ = [
    "RegistryBackend",
    "RegistryPath",
    "RegistryACL",
    "ACLDecision",
    "Actor",
    "Op",
    "RegistryAccessError",
    "default_policy",
    "RegistryTool",
]
