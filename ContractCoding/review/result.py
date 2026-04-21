from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class ReviewPacket:
    module_name: str
    files: List[str]
    module_dependencies: List[str] = field(default_factory=list)

    def render(self) -> str:
        dependencies = self._format_bullets(self.module_dependencies)
        files = self._format_bullets(self.files)
        return (
            f"Module team: {self.module_name}\n"
            "Review this completed module wave.\n"
            "Files to review:\n"
            f"{files}\n\n"
            "Resolved module dependencies:\n"
            f"{dependencies}\n\n"
            "For EACH file, read the implementation, compare it against the contract, and update the same file block.\n"
            "If correct: set Status to VERIFIED. If incorrect: set Status to ERROR and append actionable issue bullets after the Status line."
        )

    @staticmethod
    def _format_bullets(values: List[str]) -> str:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            return "- None"
        return "\n".join(f"- {value}" for value in cleaned)
