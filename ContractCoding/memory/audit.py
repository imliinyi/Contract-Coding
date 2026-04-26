import ast
import os
import re
from dataclasses import dataclass
from typing import Literal

from ContractCoding.memory.contract import ContractClass, ContractFile, ContractFunction, ContractKernel

# --- Helpers to extract file paths purely from backticks ---
_ALLOWED_EXTS = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css'}


@dataclass
class AuditIssue:
    path: str
    severity: Literal["error", "warning"]
    kind: str
    message: str
    expected: str | None = None
    actual: str | None = None

    def format(self) -> str:
        parts = [f"[{self.severity.upper()}] {self.path}: {self.kind}: {self.message}"]
        if self.expected is not None:
            parts.append(f"expected={self.expected}")
        if self.actual is not None:
            parts.append(f"actual={self.actual}")
        return " | ".join(parts)


def _strip_code_fences(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", "", text)


def _token_looks_like_path(token: str) -> bool:
    if not token:
        return False
    token = token.strip()
    if token.endswith('/'):
        return False
    last = token.split('/')[-1].strip()
    return any(last.endswith(ext) for ext in _ALLOWED_EXTS)


def _extract_directory_structure_paths(document_content: str) -> list[str]:
    paths = []
    code_blocks = re.findall(r"```(?:[\w]*\n)?([\s\S]*?)```", document_content)

    for block_content in code_blocks:
        if not ("workspace/" in block_content or "|--" in block_content or "|__" in block_content or "├──" in block_content):
            continue

        path_stack = []
        for line in block_content.splitlines():
            if not line.strip():
                continue
            match = re.match(r"^([ \t\|`\+\-\\\u2500-\u257f]*)(.*)$", line)
            if not match:
                continue
            prefix, name = match.groups()
            name = name.strip()
            if not name:
                continue
            depth = len(prefix)
            while path_stack and path_stack[-1][0] >= depth:
                path_stack.pop()
            path_stack.append((depth, name))
            if _token_looks_like_path(name):
                clean_stack_names = [p[1].rstrip('/') for p in path_stack]
                paths.append("/".join(clean_stack_names))

    return paths


def _extract_backtick_paths(document_content: str) -> list[str]:
    dir_paths = _extract_directory_structure_paths(document_content)
    doc_no_fences = _strip_code_fences(document_content)
    tokens = re.findall(r"`([^`]+)`", doc_no_fences)
    text_paths: list[str] = []
    for tok in tokens:
        for line in tok.splitlines():
            cand = line.strip()
            if _token_looks_like_path(cand):
                text_paths.append(cand)

    seen = set()
    uniq = []
    for p in dir_paths + text_paths:
        norm_p = p
        while norm_p.startswith('workspace/'):
            norm_p = norm_p.replace('workspace/', '', 1)
        if norm_p not in seen:
            seen.add(norm_p)
            uniq.append(p)
    return uniq


def _find_in_workspace(workspace_path: str, candidate: str) -> str | None:
    wp = os.path.abspath(workspace_path)
    cand = candidate.strip().replace('\\', '/')
    cand = cand.replace('\r', '').replace('\n', '')
    if cand.startswith('./'):
        cand = cand[2:]
    workspace_base = os.path.basename(wp)
    if cand.startswith(f"{workspace_base}/"):
        cand = cand[len(workspace_base) + 1 :]
    if cand.startswith('workspace/'):
        cand = cand.replace('workspace/', '')
    if cand.startswith('/'):
        cand = cand.lstrip('/')
    while cand.startswith('workspace/'):
        cand = cand.replace('workspace/', '', 1)

    if '/' in cand:
        full = os.path.abspath(os.path.join(wp, cand))
        return full if os.path.exists(full) else None
    for root, _, files in os.walk(wp):
        if cand in files:
            return os.path.join(root, cand)
    return None


def _extract_section(document_content: str, heading_regex: str) -> str:
    m = re.search(heading_regex, document_content, re.MULTILINE)
    if not m:
        return ""
    tail = document_content[m.end() :]
    n = re.search(r"^### ", tail, re.MULTILINE)
    return tail[: n.start()] if n else tail


def get_spec_files(document_content: str) -> set[str]:
    out: set[str] = set()

    def add_path(p: str):
        if not p:
            return
        q = p.strip()
        if not _token_looks_like_path(q):
            return
        while q.startswith('workspace/'):
            q = q.replace('workspace/', '', 1)
        out.add(q)

    sec = _extract_section(document_content, r"^###\s*2\.4.*$")
    search_scopes = [sec] if sec else []
    search_scopes.append(document_content)

    file_token_pat = re.compile(r"(?i)(?:\*\*\s*(?:File|Path)\s*:?-?\s*\*\*|\b(?:File|Path)\b)\s*:?-?\s*`?([A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|html|css))")
    list_file_token_pat = re.compile(r"(?i)-\s*(?:\*\*\s*(?:File|Path)\s*:?-?\s*\*\*|\b(?:File|Path)\b)\s*:?-?\s*`?([A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|html|css))")
    status_bullet_pat = re.compile(r"(?i)-\s*\*\*([^*\n]+?\.(?:py|js|ts|tsx|jsx|html|css))\*\*\s*:\s*(DONE|TODO|IN_PROGRESS|ERROR)")
    header_pat = re.compile(r"^####\s*`?([A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|html|css))", re.MULTILINE)

    for scope in search_scopes:
        if not scope:
            continue
        for pattern in (file_token_pat, list_file_token_pat, status_bullet_pat, header_pat):
            for m in pattern.finditer(scope):
                add_path(m.group(1))

    return out


def check_missing_specs(document_content: str) -> list[str]:
    dir_set = set()
    for p in _extract_directory_structure_paths(document_content):
        q = p
        while q.startswith('workspace/'):
            q = q.replace('workspace/', '', 1)
        dir_set.add(q)
    spec_set = get_spec_files(document_content)
    return sorted(x for x in dir_set if x not in spec_set)


def audit_file_existence(document_content, workspace_path):
    missing = check_missing_files(document_content, workspace_path)
    if missing:
        print("Missing files:")
        for m in missing:
            print(f"- {m}")
    else:
        print("All files exist.")


def audit_file_versions(document_content, workspace_path):
    version_mismatches = []
    section_pattern = re.compile(r"(?s)^####\s*\d+\.\s.*?(?=^####\s*\d+\.\s|^### |\Z)", re.MULTILINE)
    sections = section_pattern.findall(document_content)
    path_to_version: dict[str, int] = {}

    for sec in sections:
        paths = re.findall(r"-\s*\*\*Paths?\*\*:\s*`([^`]+)`", sec)
        if not paths:
            header_match = re.search(r"^####\s*\d+\.\s*(.*)$", sec, re.MULTILINE)
            if header_match:
                paths = re.findall(r"`([^`]+)`", header_match.group(1))
        vm = re.search(r"-\s*\*\*Version\*\*:\s*(\d+)", sec)
        if vm:
            for p in paths:
                path_to_version[p.strip()] = int(vm.group(1))

    inline_file_pattern = re.compile(r"-\s\*\*File\*\*:\s*`([^`]+)`[\s\S]*?-\s\*\*Version\*\*:\s(\d+)")
    for m in inline_file_pattern.finditer(document_content):
        path_to_version[m.group(1).strip()] = int(m.group(2))

    prd_split_pattern = re.compile(r"(\*\*File\*\*:\s*`[^`]+`)")
    parts = prd_split_pattern.split(document_content)
    for i in range(1, len(parts), 2):
        file_line = parts[i]
        content = parts[i + 1]
        path_match = re.search(r"`([^`]+)`", file_line)
        ver_match = re.search(r"[\*\-]\s*\*\*Version\*\*:\s*(\d+)", content)
        if path_match and ver_match:
            path_to_version[path_match.group(1).strip()] = int(ver_match.group(1))

    for p, doc_ver in path_to_version.items():
        resolved = _find_in_workspace(workspace_path, p)
        if not resolved:
            continue
        try:
            with open(resolved, 'r', encoding='utf-8') as f:
                first = f.readline().strip()
                mm = re.match(r"#\s*version:\s*(\d+)", first, re.IGNORECASE)
                if mm:
                    file_ver = int(mm.group(1))
                    if file_ver != doc_ver:
                        version_mismatches.append((p, file_ver, doc_ver))
                else:
                    version_mismatches.append((p, "N/A", doc_ver))
        except Exception:
            version_mismatches.append((p, "read_error", doc_ver))

    if version_mismatches:
        print("\nVersion mismatches:")
        for file, fver, dver in version_mismatches:
            print(f"- {file}: File version ({fver}) does not match document version ({dver})")
    else:
        print("\nAll file versions match the document.")


def get_documented_files(document_content: str) -> set[str]:
    documented_files = set()
    prd_split_pattern = re.compile(r"(\*\*File\*\*:\s*`[^`]+`)")
    parts = prd_split_pattern.split(document_content)
    for i in range(1, len(parts), 2):
        path_match = re.search(r"`([^`]+)`", parts[i])
        if path_match:
            documented_files.add(path_match.group(1).strip())

    inline_file_pattern = re.compile(r"-\s\*\*File\*\*:\s*`([^`]+)`")
    for m in inline_file_pattern.finditer(document_content):
        documented_files.add(m.group(1).strip())

    section_pattern = re.compile(r"(?s)^####\s*\d+\.\s.*?(?=^####\s*\d+\.\s|^### |\Z)", re.MULTILINE)
    for sec in section_pattern.findall(document_content):
        paths = re.findall(r"-\s*\*\*Paths?\*\*:\s*`([^`]+)`", sec)
        if not paths:
            header_match = re.search(r"^####\s*\d+\.\s*(.*)$", sec, re.MULTILINE)
            if header_match:
                paths = re.findall(r"`([^`]+)`", header_match.group(1))
        for p in paths:
            documented_files.add(p.strip())

    for p in _extract_directory_structure_paths(document_content):
        while p.startswith('workspace/'):
            p = p.replace('workspace/', '', 1)
        documented_files.add(p)

    return documented_files


def get_workspace_files(workspace_path: str) -> set[str]:
    wp = os.path.abspath(workspace_path)
    actual_files = set()
    for root, dirs, files in os.walk(wp):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        for f in files:
            if f.startswith('.') or f.endswith('.pyc'):
                continue
            full_path = os.path.join(root, f)
            actual_files.add(os.path.relpath(full_path, wp))
    return actual_files


def check_undocumented_files(document_content: str, workspace_path: str) -> list[str]:
    norm_documented = set()
    for p in get_documented_files(document_content):
        if p.startswith('workspace/'):
            p = p.replace('workspace/', '', 1)
        norm_documented.add(p)
    return sorted(f for f in get_workspace_files(workspace_path) if f not in norm_documented)


def check_missing_files(document_content: str, workspace_path: str) -> list[str]:
    return sorted(p for p in get_documented_files(document_content) if not _find_in_workspace(workspace_path, p))


def audit_contract_interfaces(kernel: ContractKernel, workspace_path: str) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    for contract_file in kernel.files:
        resolved = _find_in_workspace(workspace_path, contract_file.path)
        if not resolved:
            issues.append(AuditIssue(contract_file.path, "error", "missing_file", "Contract-declared file is missing"))
            continue
        if not contract_file.path.endswith('.py'):
            continue

        try:
            with open(resolved, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
        except SyntaxError as exc:
            issues.append(AuditIssue(contract_file.path, "error", "syntax_error", str(exc)))
            continue
        except Exception as exc:
            issues.append(AuditIssue(contract_file.path, "error", "read_error", str(exc)))
            continue

        _audit_placeholder_logic(contract_file.path, source, tree, issues)
        _audit_python_symbols(contract_file, tree, issues)
    return issues


def _audit_placeholder_logic(path: str, source: str, tree: ast.AST, issues: list[AuditIssue]) -> None:
    lowered = source.lower()
    if "todo" in lowered or "placeholder" in lowered:
        issues.append(AuditIssue(path, "error", "placeholder_text", "Implementation contains TODO or placeholder text"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Pass):
            issues.append(AuditIssue(path, "error", "placeholder_pass", "Concrete implementation contains pass"))
            return


def _audit_python_symbols(contract_file: ContractFile, tree: ast.AST, issues: list[AuditIssue]) -> None:
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for expected_class in contract_file.classes:
        class_node = classes.get(expected_class.name)
        if class_node is None:
            issues.append(AuditIssue(contract_file.path, "error", "missing_class", f"Missing class {expected_class.name}", expected_class.name, None))
            continue
        methods = {node.name: node for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for expected_method in expected_class.methods:
            actual_method = methods.get(expected_method.name)
            if actual_method is None:
                issues.append(AuditIssue(contract_file.path, "error", "missing_method", f"Missing method {expected_class.name}.{expected_method.name}", expected_method.signature, None))
                continue
            _compare_function(contract_file.path, expected_method, actual_method, issues, owner=expected_class.name)

    for expected_function in contract_file.functions:
        actual_function = functions.get(expected_function.name)
        if actual_function is None:
            issues.append(AuditIssue(contract_file.path, "error", "missing_function", f"Missing function {expected_function.name}", expected_function.signature, None))
            continue
        _compare_function(contract_file.path, expected_function, actual_function, issues)


def _compare_function(path: str, expected: ContractFunction, actual: ast.FunctionDef | ast.AsyncFunctionDef, issues: list[AuditIssue], owner: str | None = None) -> None:
    expected_params = [(p.name, _normalize_type(p.type)) for p in expected.params]
    actual_params = [(arg.arg, _normalize_type(_annotation_to_str(arg.annotation))) for arg in actual.args.args]

    comparable_actual = actual_params
    if comparable_actual and comparable_actual[0][0] in {"self", "cls"} and (not expected_params or expected_params[0][0] not in {"self", "cls"}):
        comparable_actual = comparable_actual[1:]

    if [name for name, _ in expected_params] != [name for name, _ in comparable_actual]:
        label = f"{owner}.{expected.name}" if owner else expected.name
        issues.append(
            AuditIssue(
                path,
                "error",
                "parameter_mismatch",
                f"Parameter names do not match for {label}",
                ", ".join(name for name, _ in expected_params),
                ", ".join(name for name, _ in comparable_actual),
            )
        )
        return

    for (expected_name, expected_type), (_, actual_type) in zip(expected_params, comparable_actual):
        if expected_type and actual_type and expected_type != actual_type:
            issues.append(
                AuditIssue(
                    path,
                    "error",
                    "parameter_type_mismatch",
                    f"Type annotation mismatch for parameter {expected_name} in {expected.name}",
                    expected_type,
                    actual_type,
                )
            )

    expected_return = _normalize_type(expected.returns)
    actual_return = _normalize_type(_annotation_to_str(actual.returns))
    if expected_return and actual_return and expected_return != actual_return:
        issues.append(
            AuditIssue(
                path,
                "error",
                "return_type_mismatch",
                f"Return annotation mismatch for {expected.name}",
                expected_return,
                actual_return,
            )
        )
    elif expected_return and not actual_return:
        issues.append(
            AuditIssue(
                path,
                "warning",
                "missing_return_annotation",
                f"Missing return annotation for {expected.name}",
                expected_return,
                None,
            )
        )


def _annotation_to_str(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    try:
        return ast.unparse(annotation)
    except Exception:
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Constant):
            return str(annotation.value)
    return None


def _normalize_type(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"\'')
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned or None
