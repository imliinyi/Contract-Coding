from __future__ import annotations

import re
from typing import Dict, List


class SchedulerDispatchBuilder:
    def build_module_owner_packet(
        self,
        module_name: str,
        owner: str,
        tasks: List[Dict[str, object]],
    ) -> str:
        ordered_tasks = sorted(tasks, key=lambda task: str(task.get("file", "")))
        target_files = [str(task.get("file", "")).strip() for task in ordered_tasks if task.get("file")]
        per_file_packets: List[str] = []

        for task in ordered_tasks:
            file_path = str(task.get("file", "Unknown"))
            contract_desc = self._extract_contract_description(task)
            issue_text = self._extract_issues(task)
            depends_on = [str(value).strip() for value in task.get("depends_on", []) if str(value).strip()]
            packet_lines = [
                f"File: {file_path}",
                "Depends on:",
                self._format_bullets(depends_on),
                "Contract summary:",
                contract_desc or "- No additional contract details beyond the file block metadata.",
            ]
            if issue_text:
                packet_lines.extend(["Existing issues to resolve:", issue_text])
            per_file_packets.append("\n".join(packet_lines))

        packets_body = "\n\n---\n\n".join(per_file_packets)
        return (
            f"Module team: {module_name}\n"
            f"Owner packet: {owner}\n"
            "Implement/Fix the ready files in this module wave.\n"
            "Target files in this module wave:\n"
            f"{self._format_bullets(target_files)}\n\n"
            "Limit implementation to the listed files for this module wave unless a minimal contract repair is required.\n"
            "All declared dependencies for these files are already satisfied for the current wave.\n\n"
            "Per-file packets:\n"
            f"{packets_body}\n\n"
            "After implementation, update each listed file block status to DONE via <document_action>."
        )

    def build_blocked_workflow_message(self, blocked_tasks: List[Dict[str, object]]) -> str:
        blocked_lines: List[str] = []
        for task in blocked_tasks[:8]:
            file_path = str(task.get("file", "Unknown"))
            module_name = str(task.get("module", "root"))
            blocked_by = [str(value).strip() for value in task.get("blocked_by", []) if str(value).strip()]
            if blocked_by:
                blocked_lines.append(f"- {file_path} (module {module_name}) blocked by: {', '.join(blocked_by)}")

        body = "\n".join(blocked_lines) if blocked_lines else "- No blocked task details were available."
        return (
            "Critical: The module-team scheduler detected blocked work with no ready wave to run. "
            "This usually means a cyclic dependency or an invalid dependency edge in the contract. "
            "Review the module DAG and fix the affected file blocks.\n\n"
            "Blocked tasks:\n"
            f"{body}"
        )

    @staticmethod
    def _extract_issues(task: Dict[str, object]) -> str:
        block = task.get("block", "") or ""
        if not block:
            return ""

        lines = str(block).split("\n")
        status_index = None
        for index, line in enumerate(lines):
            if re.search(r"\*\*Status(?::)?\*\*", line):
                status_index = index
                break
        if status_index is None:
            return ""

        issues: List[str] = []
        for line in lines[status_index + 1 :]:
            stripped = line.rstrip()
            if not stripped:
                continue
            if re.search(r"\*\*(Owner|Version|Status|Class|Function|Methods|Attributes|Module|Depends On)\*\*", stripped):
                continue
            issues.append(stripped)
        return "\n".join(issues).strip()

    @staticmethod
    def _extract_contract_description(task: Dict[str, object], max_lines: int = 30) -> str:
        block = str(task.get("block", "") or "").strip("\n")
        if not block:
            return ""

        skip_fields = re.compile(r"\*\*\s*(Owner|Version|Status)\s*:?\s*\*\*", re.IGNORECASE)
        out: List[str] = []
        for raw_line in block.split("\n"):
            line = raw_line.rstrip()
            if not line.strip():
                continue
            if line.strip().startswith("**File:**"):
                continue
            if skip_fields.search(line):
                continue
            out.append(line)
            if len(out) >= max_lines:
                break
        return "\n".join(out).strip()

    @staticmethod
    def _format_bullets(values: List[str], empty_value: str = "- None") -> str:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if not cleaned:
            return empty_value
        return "\n".join(f"- {value}" for value in cleaned)
