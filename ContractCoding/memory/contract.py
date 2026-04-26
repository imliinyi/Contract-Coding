import re
from dataclasses import dataclass, field
from typing import Literal

ContractStatus = Literal["TODO", "IN_PROGRESS", "DONE", "ERROR", "VERIFIED"]

VALID_STATUSES = {"TODO", "IN_PROGRESS", "DONE", "ERROR", "VERIFIED"}


@dataclass
class ContractParam:
    name: str
    type: str | None = None


@dataclass
class ContractFunction:
    name: str
    params: list[ContractParam] = field(default_factory=list)
    returns: str | None = None
    signature: str = ""


@dataclass
class ContractAttribute:
    name: str
    type: str | None = None
    description: str = ""


@dataclass
class ContractClass:
    name: str
    attributes: list[ContractAttribute] = field(default_factory=list)
    methods: list[ContractFunction] = field(default_factory=list)


@dataclass
class ContractFile:
    path: str
    owner: str
    status: ContractStatus
    version: int
    classes: list[ContractClass] = field(default_factory=list)
    functions: list[ContractFunction] = field(default_factory=list)
    block: str = ""


@dataclass
class ContractKernel:
    files: list[ContractFile] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)

    def by_path(self) -> dict[str, ContractFile]:
        return {f.path: f for f in self.files}


@dataclass
class ContractParseIssue:
    path: str
    field: str
    reason: str

    def format(self) -> str:
        return f"{self.path}: {self.field} - {self.reason}"


class ContractParseError(ValueError):
    def __init__(self, issues: list[ContractParseIssue]):
        self.issues = issues
        super().__init__("; ".join(issue.format() for issue in issues))


def normalize_contract_path(path: str) -> str:
    value = (path or "").strip().strip("`").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    while value.startswith("workspace/"):
        value = value[len("workspace/") :]
    return value.strip("/")


def parse_contract_kernel(markdown: str) -> ContractKernel:
    files, issues = _parse_symbolic_api_files(markdown or "")
    if issues:
        raise ContractParseError(issues)
    dependencies = _parse_dependencies(markdown or "", {f.path for f in files})
    return ContractKernel(files=files, dependencies=dependencies)


def _extract_section(markdown: str, heading_pattern: str) -> str:
    match = re.search(heading_pattern, markdown, re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    tail = markdown[match.end() :]
    next_heading = re.search(r"^###\s+", tail, re.MULTILINE)
    return tail[: next_heading.start()] if next_heading else tail


def _parse_symbolic_api_files(markdown: str) -> tuple[list[ContractFile], list[ContractParseIssue]]:
    section = _extract_section(markdown, r"^###\s*(?:2\.4\s*)?Symbolic API Specifications.*$")
    search_text = section or markdown
    file_pattern = re.compile(r"^\s*(?:[-*]\s*)?\*\*File:\*\*\s*`?([^`\n]+)`?\s*$", re.IGNORECASE | re.MULTILINE)
    matches = list(file_pattern.finditer(search_text))
    files: list[ContractFile] = []
    issues: list[ContractParseIssue] = []

    for index, match in enumerate(matches):
        path = normalize_contract_path(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(search_text)
        block = search_text[start:end].strip("\n")
        parsed = _parse_file_block(path, block, issues)
        if parsed:
            files.append(parsed)

    return files, issues


def _parse_file_block(path: str, block: str, issues: list[ContractParseIssue]) -> ContractFile | None:
    owner = _match_field(block, "Owner")
    status = _match_field(block, "Status")
    version_raw = _match_field(block, "Version")

    if not path:
        issues.append(ContractParseIssue("Unknown", "File", "missing file path"))
        return None
    if not owner:
        issues.append(ContractParseIssue(path, "Owner", "missing Owner field"))
    if not status:
        issues.append(ContractParseIssue(path, "Status", "missing Status field"))
    if not version_raw:
        issues.append(ContractParseIssue(path, "Version", "missing Version field"))

    status_clean = (status or "").strip().strip("` ").upper()
    if status_clean and status_clean not in VALID_STATUSES:
        issues.append(ContractParseIssue(path, "Status", f"invalid status {status_clean!r}"))

    version = 0
    if version_raw:
        version_match = re.search(r"\d+", version_raw)
        if version_match:
            version = int(version_match.group(0))
        else:
            issues.append(ContractParseIssue(path, "Version", f"invalid version {version_raw!r}"))

    if any(issue.path == path for issue in issues):
        return None

    classes, functions = _parse_symbols(block)
    return ContractFile(
        path=path,
        owner=(owner or "").strip().replace(" ", "_"),
        status=status_clean,  # type: ignore[arg-type]
        version=version,
        classes=classes,
        functions=functions,
        block=(f"**File:** `{path}`\n" + block).strip(),
    )


def _match_field(block: str, field_name: str) -> str | None:
    pattern = re.compile(
        rf"(?:^|\n)\s*(?:[-*]\s*)?\*\*{re.escape(field_name)}(?::)?\*\*\s*:??\s*(.+)",
        re.IGNORECASE,
    )
    match = pattern.search(block)
    if not match:
        return None
    return match.group(1).strip().strip("`").strip()


def _parse_symbols(block: str) -> tuple[list[ContractClass], list[ContractFunction]]:
    classes: list[ContractClass] = []
    functions: list[ContractFunction] = []
    current_class: ContractClass | None = None
    in_attributes = False

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        class_match = re.search(r"\*\*Class(?::)?\*\*\s*:??\s*`?([A-Za-z_]\w*)`?", line, re.IGNORECASE)
        if not class_match:
            class_match = re.search(r"Class Name\s*[\"`]?([A-Za-z_]\w*)[\"`]?")
        if class_match:
            current_class = ContractClass(name=class_match.group(1))
            classes.append(current_class)
            in_attributes = False
            continue

        if re.search(r"\*\*Attributes(?::)?\*\*", line, re.IGNORECASE):
            in_attributes = True
            continue
        if re.search(r"\*\*Methods(?::)?\*\*|\*\*Functions(?::)?\*\*", line, re.IGNORECASE):
            in_attributes = False
            continue

        sig = _extract_signature(line)
        if sig:
            func = parse_signature(sig)
            if current_class is not None:
                current_class.methods.append(func)
            else:
                functions.append(func)
            continue

        if in_attributes and current_class is not None:
            attr = _parse_attribute(line)
            if attr:
                current_class.attributes.append(attr)

    return classes, functions


def _extract_signature(line: str) -> str | None:
    backtick_match = re.search(r"`([^`]*def\s+[^`]+)`", line)
    if backtick_match:
        return backtick_match.group(1).strip()
    quote_match = re.search(r"Signature\s*[\"`]([^\"`]*def\s+[^\"`]+)[\"`]", line, re.IGNORECASE)
    if quote_match:
        return quote_match.group(1).strip()
    plain_match = re.search(r"(def\s+[A-Za-z_]\w*\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:?)", line)
    return plain_match.group(1).strip() if plain_match else None


def parse_signature(signature: str) -> ContractFunction:
    clean = signature.strip().rstrip(":")
    match = re.match(r"def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:->\s*(.+))?$", clean)
    if not match:
        return ContractFunction(name=clean, signature=signature)
    params = [_parse_param(part) for part in match.group(2).split(",") if part.strip()]
    returns = (match.group(3) or "").strip() or None
    return ContractFunction(name=match.group(1), params=params, returns=returns, signature=signature)


def _parse_param(param: str) -> ContractParam:
    left = param.strip().split("=", 1)[0].strip()
    if ":" in left:
        name, type_name = left.split(":", 1)
        return ContractParam(name=name.strip(), type=type_name.strip() or None)
    return ContractParam(name=left.strip(), type=None)


def _parse_attribute(line: str) -> ContractAttribute | None:
    match = re.search(r"`?([A-Za-z_]\w*)`?\s*:\s*`?([^`\s-]+)`?\s*(?:-|:)?\s*(.*)", line)
    if not match:
        return None
    return ContractAttribute(name=match.group(1), type=match.group(2), description=match.group(3).strip())


def _parse_dependencies(markdown: str, known_files: set[str]) -> dict[str, list[str]]:
    section = _extract_section(markdown, r"^###\s*(?:2\.3\s*)?Dependency Relationships.*$")
    dependencies: dict[str, list[str]] = {path: [] for path in known_files}
    if not section:
        return dependencies

    path_re = re.compile(r"`?([A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|html|css))`?")
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        paths = [normalize_contract_path(p) for p in path_re.findall(line)]
        paths = [p for p in paths if p in known_files]
        if len(paths) < 2:
            continue
        source = paths[0]
        deps = paths[1:]
        lowered = line.lower()
        if "depends on" in lowered or "depend on" in lowered or "requires" in lowered or "->" in line or "-->" in line or ":" in line:
            for dep in deps:
                if dep != source and dep not in dependencies[source]:
                    dependencies[source].append(dep)
    return dependencies


def _document_manager_get_kernel(self) -> ContractKernel:
    return parse_contract_kernel(self.get())


try:
    from ContractCoding.memory.document import DocumentManager

    if not hasattr(DocumentManager, "get_kernel"):
        DocumentManager.get_kernel = _document_manager_get_kernel  # type: ignore[attr-defined]
except Exception:
    pass
