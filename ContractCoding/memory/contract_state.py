"""Structured contract state used by orchestration and document rendering."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Optional, Tuple


STATUS_VALUES = {"TODO", "IN_PROGRESS", "ERROR", "DONE", "VERIFIED"}

SECTION_SPECS = [
    {
        "key": "Project Overview",
        "heading": "### 1.1 Project Overview",
        "aliases": ["### Project Overview"],
    },
    {
        "key": "User Stories (Features)",
        "heading": "### 1.2 User Stories (Features)",
        "aliases": ["### User Stories (Features)"],
    },
    {
        "key": "Constraints",
        "heading": "### 1.3 Constraints",
        "aliases": ["### Constraints"],
    },
    {
        "key": "Directory Structure",
        "heading": "### 2.1 Directory Structure",
        "aliases": ["### Directory Structure"],
    },
    {
        "key": "Global Shared Knowledge",
        "heading": "### 2.2 Global Shared Knowledge",
        "aliases": ["### Global Shared Knowledge"],
    },
    {
        "key": "Dependency Relationships",
        "heading": "### 2.3 Dependency Relationships(MUST):",
        "aliases": ["### Dependency Relationships(MUST):", "### Dependency Relationships"],
    },
    {
        "key": "Symbolic API Specifications",
        "heading": "### 2.4 Symbolic API Specifications",
        "aliases": ["### Symbolic API Specifications"],
    },
    {
        "key": "Status Model & Termination Guard",
        "heading": "### Status Model & Termination Guard",
        "aliases": [],
    },
]

SECTION_ORDER = [spec["key"] for spec in SECTION_SPECS]
SECTION_HEADINGS = {spec["key"]: spec["heading"] for spec in SECTION_SPECS}
HEADING_TO_KEY: Dict[str, str] = {}
for spec in SECTION_SPECS:
    HEADING_TO_KEY[spec["heading"]] = spec["key"]
    for alias in spec["aliases"]:
        HEADING_TO_KEY[alias] = spec["key"]

FILE_LINE_RE = re.compile(r"^\*\*File:\*\*\s*`?([^`]+)`?\s*$")


def canonicalize_section_key(raw_key: str) -> Optional[str]:
    if not raw_key:
        return None

    key = str(raw_key).strip()
    if key in SECTION_HEADINGS:
        return key
    if key in HEADING_TO_KEY:
        return HEADING_TO_KEY[key]

    normalized = key.lower().replace("###", "").strip()
    normalized = re.sub(r"^\d+(?:\.\d+)?\s+", "", normalized)
    normalized = normalized.replace("(must)", "").replace("must", "")
    normalized = normalized.replace(":", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    aliases = {
        "project overview": "Project Overview",
        "user stories": "User Stories (Features)",
        "user stories (features)": "User Stories (Features)",
        "features": "User Stories (Features)",
        "constraints": "Constraints",
        "directory structure": "Directory Structure",
        "global shared knowledge": "Global Shared Knowledge",
        "dependency relationships": "Dependency Relationships",
        "symbolic api specifications": "Symbolic API Specifications",
        "status model & termination guard": "Status Model & Termination Guard",
        "status model": "Status Model & Termination Guard",
        "termination guard": "Status Model & Termination Guard",
    }
    return aliases.get(normalized)


def _strip_empty_edges(lines: List[str]) -> List[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


@dataclass
class TaskBlock:
    file: str
    lines: List[str]
    owner: str = "Unknown"
    status: str = "TODO"
    version: Optional[int] = None

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> "TaskBlock":
        block_lines = [line.rstrip("\n") for line in lines]
        file_path = ""
        owner = "Unknown"
        status = "TODO"
        version: Optional[int] = None

        for line in block_lines:
            file_match = FILE_LINE_RE.match(line.strip())
            if file_match:
                file_path = file_match.group(1).strip()
                continue

            owner_match = re.search(r"\*\*Owner(?::)?\*\*[: ]+(.+)", line)
            if owner_match:
                owner = owner_match.group(1).strip().replace(" ", "_")
                continue

            status_match = re.search(r"\*\*Status(?::)?\*\*[: ]+(.+)", line)
            if status_match:
                raw_status = status_match.group(1).strip()
                status = raw_status if raw_status in STATUS_VALUES else "TODO"
                continue

            version_match = re.search(r"\*\*Version(?::)?\*\*[: ]+(\d+)", line)
            if version_match:
                version = int(version_match.group(1))

        return cls(file=file_path, lines=block_lines, owner=owner, status=status, version=version)

    def copy(self) -> "TaskBlock":
        return TaskBlock(
            file=self.file,
            lines=list(self.lines),
            owner=self.owner,
            status=self.status,
            version=self.version,
        )

    def render(self) -> str:
        return "\n".join(self.lines).strip("\n")

    def _update_field(self, field_name: str, value: str) -> None:
        pattern = re.compile(rf"^(\*\s*\*\*{re.escape(field_name)}(?::)?\*\*[: ]+).*$")
        replacement = f"*   **{field_name}:** {value}"
        for index, line in enumerate(self.lines):
            if pattern.match(line.strip()):
                self.lines[index] = replacement
                return
        self.lines.append(replacement)

    def set_status(self, status: str) -> None:
        self.status = status
        self._update_field("Status", status)

    def set_owner(self, owner: str) -> None:
        self.owner = owner
        self._update_field("Owner", owner)

    def set_version(self, version: int) -> None:
        self.version = version
        self._update_field("Version", str(version))

    def append_issues(self, issues: Iterable[str]) -> None:
        cleaned = [issue.strip() for issue in issues if issue and issue.strip()]
        if not cleaned:
            return

        normalized_existing = {line.strip() for line in self.lines}
        status_index = 0
        for index, line in enumerate(self.lines):
            if "**Status" in line:
                status_index = index + 1
                break

        new_issue_lines = []
        for issue in cleaned:
            bullet = issue if issue.startswith("- ") else f"- {issue}"
            if bullet.strip() not in normalized_existing:
                new_issue_lines.append(bullet)

        if new_issue_lines:
            self.lines[status_index:status_index] = new_issue_lines

    def to_record(self) -> Dict[str, object]:
        return {
            "file": self.file,
            "owner": self.owner,
            "status": self.status,
            "version": self.version,
            "block": self.render(),
        }


def _split_symbolic_body(body_text: str) -> Tuple[List[str], List[str], Dict[str, TaskBlock]]:
    lines = [line.rstrip("\n") for line in body_text.splitlines()]
    preamble: List[str] = []
    order: List[str] = []
    blocks: Dict[str, TaskBlock] = {}
    current: List[str] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        task = TaskBlock.from_lines(current)
        if task.file:
            blocks[task.file] = task
            order.append(task.file)
        current = []

    for line in lines:
        if FILE_LINE_RE.match(line.strip()):
            flush()
            current = [line]
            continue
        if current:
            current.append(line)
        else:
            preamble.append(line)
    flush()
    return _strip_empty_edges(preamble), order, blocks


@dataclass
class ContractState:
    sections: Dict[str, str] = field(default_factory=dict)
    symbolic_preamble: str = ""
    task_order: List[str] = field(default_factory=list)
    tasks: Dict[str, TaskBlock] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "ContractState":
        return cls(
            sections={key: "" for key in SECTION_ORDER if key != "Symbolic API Specifications"},
            symbolic_preamble="",
            task_order=[],
            tasks={},
        )

    def copy(self) -> "ContractState":
        clone = ContractState.empty()
        clone.sections = dict(self.sections)
        clone.symbolic_preamble = self.symbolic_preamble
        clone.task_order = list(self.task_order)
        clone.tasks = {file_path: task.copy() for file_path, task in self.tasks.items()}
        return clone

    @classmethod
    def from_markdown(cls, markdown: str) -> "ContractState":
        state = cls.empty()
        if not markdown or not markdown.strip():
            return state

        section_lines: Dict[str, List[str]] = {key: [] for key in SECTION_ORDER}
        current_section: Optional[str] = None
        for raw_line in markdown.splitlines():
            line = raw_line.rstrip("\n")
            canonical = HEADING_TO_KEY.get(line.strip())
            if canonical:
                current_section = canonical
                continue
            if current_section:
                section_lines[current_section].append(line)

        for section_key, lines in section_lines.items():
            body = "\n".join(lines).strip("\n")
            if section_key == "Symbolic API Specifications":
                preamble, order, blocks = _split_symbolic_body(body)
                state.symbolic_preamble = "\n".join(preamble).strip("\n")
                state.task_order = order
                state.tasks = blocks
            elif section_key in state.sections:
                state.sections[section_key] = body

        return state

    def to_markdown(self) -> str:
        lines: List[str] = ["## Product Requirement Document (PRD)", ""]
        for key in SECTION_ORDER:
            if key == "Directory Structure":
                lines.extend(["## Technical Architecture Document (System Design)", ""])
            heading = SECTION_HEADINGS[key]
            lines.append(heading)
            body = self.get_section_body(key)
            if body:
                lines.extend(body.splitlines())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def get_section_body(self, section_key: str) -> str:
        if section_key == "Symbolic API Specifications":
            parts: List[str] = []
            if self.symbolic_preamble.strip():
                parts.append(self.symbolic_preamble.strip("\n"))
            for index, file_path in enumerate(self.task_order):
                task = self.tasks.get(file_path)
                if not task:
                    continue
                parts.append(task.render())
            return "\n\n".join(part for part in parts if part).strip("\n")
        return self.sections.get(section_key, "").strip("\n")

    def replace_section_body(self, section_key: str, body: str) -> None:
        canonical = canonicalize_section_key(section_key)
        if not canonical:
            return
        body = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if canonical == "Symbolic API Specifications":
            preamble, order, blocks = _split_symbolic_body(body)
            self.symbolic_preamble = "\n".join(preamble).strip("\n")
            for file_path in order:
                self.upsert_task(blocks[file_path], keep_order=True)
        else:
            self.sections[canonical] = body

    def append_to_section(self, section_key: str, content: str) -> None:
        canonical = canonicalize_section_key(section_key)
        if not canonical:
            return
        content = content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not content:
            return
        if canonical == "Symbolic API Specifications":
            preamble, order, blocks = _split_symbolic_body(content)
            if preamble:
                prefix = self.symbolic_preamble.strip("\n")
                joined = "\n\n".join(part for part in [prefix, "\n".join(preamble).strip("\n")] if part)
                self.symbolic_preamble = joined.strip("\n")
            for file_path in order:
                self.upsert_task(blocks[file_path], keep_order=True)
        else:
            existing = self.sections.get(canonical, "").strip("\n")
            self.sections[canonical] = "\n\n".join(part for part in [existing, content] if part).strip("\n")

    def replace_full_document(self, markdown: str) -> None:
        replacement = ContractState.from_markdown(markdown)
        self.sections = replacement.sections
        self.symbolic_preamble = replacement.symbolic_preamble
        self.task_order = replacement.task_order
        self.tasks = replacement.tasks

    def upsert_task(self, task: TaskBlock, keep_order: bool = True) -> None:
        if not task.file:
            return
        self.tasks[task.file] = task.copy()
        if task.file not in self.task_order:
            self.task_order.append(task.file)
        elif not keep_order:
            self.task_order = [path for path in self.task_order if path != task.file] + [task.file]

    def get_task(self, file_path: str) -> Optional[TaskBlock]:
        return self.tasks.get(file_path)

    def list_tasks(self) -> List[Dict[str, object]]:
        records = []
        for file_path in self.task_order:
            task = self.tasks.get(file_path)
            if task:
                records.append(task.to_record())
        return records

    def get_tasks_by_owner(self, owner: str) -> List[TaskBlock]:
        return [task.copy() for task in self.tasks.values() if task.owner == owner]

    def record_task_failure(self, file_path: str, issues: Iterable[str], owner: Optional[str] = None) -> None:
        task = self.tasks.get(file_path)
        if task is None:
            lines = [
                f"**File:** `{file_path}`",
                f"*   **Owner:** {owner or 'Unknown'}",
                "*   **Version:** 1",
                "*   **Status:** ERROR",
            ]
            task = TaskBlock.from_lines(lines)
            self.upsert_task(task)
        if owner and task.owner == "Unknown":
            task.set_owner(owner)
        if task.version is None:
            task.set_version(1)
        task.set_status("ERROR")
        task.append_issues(issues)
        self.upsert_task(task)

    def update_task_status(self, file_path: str, status: str, issues: Optional[Iterable[str]] = None) -> None:
        task = self.tasks.get(file_path)
        if not task:
            return
        task.set_status(status)
        if issues:
            task.append_issues(issues)
        self.upsert_task(task)
