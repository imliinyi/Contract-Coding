"""Interaction memory — natural-language recall.

Distinct from the audit-grade `EventLog`:
  - `EventLog` is structured machine-consumed (typed kinds; jsonl).
  - `InteractionLog` records natural-language exchanges between roles
    (user ↔ coordinator, coordinator ↔ team, reviewer ↔ implementer) so a
    later turn can recall *why* a particular framing was chosen.
  - `ContractOperation` records are the only exchange format that can affect
    scheduling or contract state.

Persisted under `/memory/<team>/interactions.jsonl` (or `/memory/global/`
for cross-team interactions). Append-only by convention; the registry ACL
is the same path-scoped policy that protects ledgers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Dict, List, Optional

from ..core.margin import MarginAnnotation


@dataclass
class Interaction:
    """One Q/A or note exchanged between two roles.

    `kind` is a short tag — common values:
      "qna"         — direct question + answer
      "clarify"     — coordinator clarifying intent
      "handover"    — capsule handover between teams
      "user_steer"  — human-in-the-loop directive
    """

    interaction_id: str
    kind: str
    speaker_role: str
    speaker_team: str
    addressed_role: str = ""
    addressed_team: str = ""
    text: str = ""
    margin: MarginAnnotation = field(default_factory=MarginAnnotation.system)
    references: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_record(self) -> Dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "kind": self.kind,
            "speaker_role": self.speaker_role,
            "speaker_team": self.speaker_team,
            "addressed_role": self.addressed_role,
            "addressed_team": self.addressed_team,
            "text": self.text,
            "margin": self.margin.to_record(),
            "references": list(self.references),
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "Interaction":
        payload = dict(payload or {})
        return cls(
            interaction_id=str(
                payload.get("interaction_id", "") or f"int:{uuid.uuid4().hex[:10]}"
            ),
            kind=str(payload.get("kind", "qna")),
            speaker_role=str(payload.get("speaker_role", "")),
            speaker_team=str(payload.get("speaker_team", "")),
            addressed_role=str(payload.get("addressed_role", "")),
            addressed_team=str(payload.get("addressed_team", "")),
            text=str(payload.get("text", "")),
            margin=MarginAnnotation.from_mapping(payload.get("margin", {}) or {}),
            references=[str(v) for v in payload.get("references", []) or []],
            created_at=float(payload.get("created_at", time.time())),
        )


@dataclass
class InteractionLog:
    """In-memory façade over a jsonl file owned by RegistryTool."""

    team_id: str
    items: List[Interaction] = field(default_factory=list)

    def append(self, interaction: Interaction) -> Interaction:
        self.items.append(interaction)
        return interaction

    def latest(
        self,
        *,
        kind: Optional[str] = None,
        speaker_role: str = "",
        n: int = 20,
    ) -> List[Interaction]:
        out = self.items
        if kind is not None:
            out = [i for i in out if i.kind == kind]
        if speaker_role:
            out = [i for i in out if i.speaker_role == speaker_role]
        return out[-n:]

    def to_record(self) -> Dict[str, Any]:
        return {"team_id": self.team_id, "items": [i.to_record() for i in self.items]}

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "InteractionLog":
        payload = dict(payload or {})
        return cls(
            team_id=str(payload.get("team_id", "")),
            items=[Interaction.from_mapping(v) for v in payload.get("items", []) or []],
        )
