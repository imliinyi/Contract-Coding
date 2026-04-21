"""Structured contract state used by orchestration and document rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


STATUS_VALUES = {"TODO", "IN_PROGRESS", "ERROR", "DONE", "VERIFIED"}
TERMINAL_STATUS_VALUES = {"DONE", "VERIFIED"}
EXECUTION_MODE_VALUES = {"single", "serial", "parallel", "team"}

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


def normalize_execution_mode(raw_value: str | None) -> Optional[str]:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip().lower()
    if not normalized:
        return None
    if normalized in {"serial", "single"}:
        return "single"
    if normalized in {"parallel", "team"}:
        return "parallel"
    return normalized if normalized in EXECUTION_MODE_VALUES else None


def _strip_empty_edges(lines: List[str]) -> List[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _dedupe_preserve_order(items: Sequence[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        cleaned = str(item).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _split_dependency_tokens(raw_value: str) -> List[str]:
    if not raw_value:
        return []

    candidates = re.findall(r"`([^`]+)`", raw_value)
    if not candidates:
        candidates = re.split(r",|;|\||->|=>|\n", raw_value)

    normalized: List[str] = []
    for candidate in candidates:
        token = str(candidate).strip().strip("-").strip()
        if not token:
            continue
        token = token.replace("\\", "/")
        token = re.sub(r"\s+", " ", token).strip()
        normalized.append(token)
    return _dedupe_preserve_order(normalized)


def _default_module_name(file_path: str) -> str:
    normalized = str(file_path).strip().replace("\\", "/")
    parent = os.path.dirname(normalized).replace("\\", "/").strip("./")
    if parent:
        return parent

    stem, _ = os.path.splitext(os.path.basename(normalized))
    if "_" in stem:
        return stem.split("_", 1)[0]
    if "-" in stem:
        return stem.split("-", 1)[0]
    return stem or normalized


def _normalize_dependency_reference(reference: str) -> str:
    return str(reference).strip().replace("\\", "/")


@dataclass
class TaskBlock:
    file: str
    lines: List[str]
    owner: str = "Unknown"
    status: str = "TODO"
    version: Optional[int] = None
    module: str = ""
    depends_on: List[str] = field(default_factory=list)
    execution_mode: Optional[str] = None

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> "TaskBlock":
        block_lines = [line.rstrip("\n") for line in lines]
        file_path = ""
        owner = "Unknown"
        status = "TODO"
        version: Optional[int] = None
        module = ""
        depends_on: List[str] = []
        execution_mode: Optional[str] = None

        for line in block_lines:
            stripped = line.strip()
            file_match = FILE_LINE_RE.match(stripped)
            if file_match:
                file_path = file_match.group(1).strip().replace("\\", "/")
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
                continue

            module_match = re.search(r"\*\*Module(?:\s*Cell)?(?::)?\*\*[: ]+(.+)", line)
            if module_match:
                module = module_match.group(1).strip()
                continue

            depends_match = re.search(r"\*\*(?:Depends On|Dependencies)(?::)?\*\*[: ]+(.+)", line)
            if depends_match:
                depends_on = _split_dependency_tokens(depends_match.group(1))
                continue

            execution_match = re.search(r"\*\*(?:Execution(?: Mode)?|Cell Mode)(?::)?\*\*[: ]+(.+)", line)
            if execution_match:
                execution_mode = normalize_execution_mode(execution_match.group(1))

        return cls(
            file=file_path,
            lines=block_lines,
            owner=owner,
            status=status,
            version=version,
            module=module,
            depends_on=depends_on,
            execution_mode=execution_mode,
        )

    def copy(self) -> "TaskBlock":
        return TaskBlock(
            file=self.file,
            lines=list(self.lines),
            owner=self.owner,
            status=self.status,
            version=self.version,
            module=self.module,
            depends_on=list(self.depends_on),
            execution_mode=self.execution_mode,
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

    def set_module(self, module: str) -> None:
        self.module = module.strip()
        self._update_field("Module", self.module)

    def set_depends_on(self, depends_on: Iterable[str]) -> None:
        self.depends_on = _dedupe_preserve_order(_normalize_dependency_reference(dep) for dep in depends_on)
        self._update_field("Depends On", ", ".join(self.depends_on))

    def set_execution_mode(self, execution_mode: str) -> None:
        normalized = normalize_execution_mode(execution_mode)
        self.execution_mode = normalized
        if normalized:
            self._update_field("Execution", normalized)

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
            "module": self.module or _default_module_name(self.file),
            "depends_on": list(self.depends_on),
            "execution_mode": self.execution_mode,
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
class ModuleCell:
    name: str
    files: List[str] = field(default_factory=list)
    owners: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    execution_mode: str = "single"
    tasks: List[TaskBlock] = field(default_factory=list)

    def aggregate_status(self) -> str:
        statuses = [task.status for task in self.tasks]
        if statuses and all(status == "VERIFIED" for status in statuses):
            return "VERIFIED"
        if statuses and all(status in TERMINAL_STATUS_VALUES for status in statuses):
            return "DONE"
        if any(status == "ERROR" for status in statuses):
            return "ERROR"
        if any(status == "IN_PROGRESS" for status in statuses):
            return "IN_PROGRESS"
        return "TODO"

    def to_record(self) -> Dict[str, object]:
        return {
            "module": self.name,
            "files": list(self.files),
            "owners": list(self.owners),
            "dependencies": list(self.dependencies),
            "execution_mode": self.execution_mode,
            "status": self.aggregate_status(),
            "tasks": [task.to_record() for task in self.tasks],
        }


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
            for file_path in self.task_order:
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

    def get_tasks_by_module(self, module_name: str) -> List[TaskBlock]:
        normalized = str(module_name).strip()
        return [
            task.copy()
            for task in self.tasks.values()
            if (task.module or _default_module_name(task.file)) == normalized
        ]

    def list_modules(self) -> List[Dict[str, object]]:
        return [cell.to_record() for cell in self.build_module_cells()]

    def get_module(self, module_name: str) -> Optional[ModuleCell]:
        normalized = str(module_name).strip()
        for cell in self.build_module_cells():
            if cell.name == normalized:
                return cell
        return None

    def build_module_cells(self) -> List[ModuleCell]:
        module_cells: Dict[str, ModuleCell] = {}
        file_to_module: Dict[str, str] = {}

        for file_path in self.task_order:
            task = self.tasks.get(file_path)
            if not task:
                continue
            module_name = task.module.strip() if task.module else _default_module_name(file_path)
            file_to_module[file_path] = module_name
            cell = module_cells.setdefault(module_name, ModuleCell(name=module_name))
            cell.files.append(file_path)
            cell.tasks.append(task.copy())

        ordered_module_names: List[str] = []
        seen = set()
        for file_path in self.task_order:
            module_name = file_to_module.get(file_path)
            if module_name and module_name not in seen:
                seen.add(module_name)
                ordered_module_names.append(module_name)

        for module_name in ordered_module_names:
            cell = module_cells[module_name]
            owners = sorted({task.owner for task in cell.tasks if task.owner != "Unknown"})
            cell.owners = owners or ["Unknown"]

            explicit_modes = [mode for mode in (normalize_execution_mode(task.execution_mode) for task in cell.tasks) if mode]
            if explicit_modes:
                cell.execution_mode = "single" if "single" in explicit_modes else "parallel"
            else:
                cell.execution_mode = "parallel" if len(cell.owners) > 1 or len(cell.files) > 1 else "single"

            dependencies: List[str] = []
            seen_dependencies = set()
            for task in cell.tasks:
                for dependency in task.depends_on:
                    normalized_dependency = _normalize_dependency_reference(dependency)
                    mapped_dependency = file_to_module.get(normalized_dependency, normalized_dependency)
                    if mapped_dependency == module_name or mapped_dependency in seen_dependencies:
                        continue
                    seen_dependencies.add(mapped_dependency)
                    dependencies.append(mapped_dependency)
            cell.dependencies = dependencies

        return [module_cells[module_name] for module_name in ordered_module_names]

    def record_task_failure(self, file_path: str, issues: Iterable[str], owner: Optional[str] = None) -> None:
        task = self.tasks.get(file_path)
        if task is None:
            lines = [
                f"**File:** `{file_path}`",
                f"*   **Owner:** {owner or 'Unknown'}",
                f"*   **Module:** {_default_module_name(file_path)}",
                "*   **Version:** 1",
                "*   **Status:** ERROR",
            ]
            task = TaskBlock.from_lines(lines)
            self.upsert_task(task)
        if owner and task.owner == "Unknown":
            task.set_owner(owner)
        if not task.module:
            task.set_module(_default_module_name(file_path))
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
